# Generic/Built-in
import numpy as np
import copy
import matplotlib
import seaborn
from math import pi
from typing import Union, List
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import Any
# Owned
import hisim.component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components.loadprofilegenerator_connector import Occupancy
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.components import controller_l1_generic_runtime
import hisim.log as log

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
    name : str
    source_weight : int
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
                  name : str,
                  source_weight : int,
                  volume : float,
                  surface : float,
                  u_value : float,
                  T_warmwater : float, 
                  T_drainwater : float, 
                  power : float, 
                  efficiency : float,
                  fuel : str ) :
        self.name = name
        self.source_weight = source_weight
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

    def clone(self) -> Any:
        return BoilerState( self.timestep, self.volume_in_l, self.temperature_in_K)

    def energy_from_temperature( self ) -> float:
        "converts temperature of storage (K) into energy contained in storage (kJ)"
        #0.977 is the density of water in kg/l
        #4.182 is the specific heat of water in kJ / ( K * kg )
        return self.temperature_in_K * self.volume_in_l * 0.977 * 4.182 #energy given in kJ


    def set_temperature_from_energy( self, energy_in_kJ: float) -> None:
        "converts energy contained in storage (kJ) into temperature (K)"
        #0.977 is the density of water in kg/l
        #4.182 is the specific heat of water in kJ / ( K * kg )
        self.temperature_in_K = energy_in_kJ / ( self.volume_in_l * 0.977 * 4.182 ) #temperature given in K
    
class Boiler( cp.Component ):
    """
    Simple boiler implementation - energy bucket model: extracts energy, adds energy and converts back to temperatere
    The boiler considers a simple heating with constant power. It relies on two input signals: hot water demand and a
    control signal for heating and outputs electricity demand or hydrogen demand as well as the boiler temperature.  
    
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
    
    def __init__( self, my_simulation_parameters: SimulationParameters, config : BoilerConfig ) -> None:

        super().__init__( config.name + str( config.source_weight ), my_simulation_parameters = my_simulation_parameters )
        
        self.build( config )
        
        #inputs
        self.WaterConsumptionC : cp.ComponentInput = self.add_input(self.component_name,
                                                                    self.WaterConsumption,
                                                                    lt.LoadTypes.WARM_WATER,
                                                                    lt.Units.LITER,
                                                                    mandatory = True)
        
        self.l1_DeviceSignalC : cp.ComponentInput = self.add_input(self.component_name,
                                                                   self.l1_DeviceSignal,
                                                                   lt.LoadTypes.ON_OFF,
                                                                   lt.Units.BINARY,
                                                                   mandatory = True)
        self.l1_RunTimeSignalC : cp.ComponentInput = self.add_input(self.component_name,
                                                                    self.l1_RunTimeSignal,
                                                                    lt.LoadTypes.ANY,
                                                                    lt.Units.ANY,
                                                                    mandatory = False)
        
        #Outputs
        self.TemperatureMeanC : cp.ComponentOutput = self.add_output(self.component_name,
                                                                     self.TemperatureMean,
                                                                     lt.LoadTypes.TEMPERATURE,
                                                                     lt.Units.CELSIUS)
        
        if self.fuel == 'electricity':
            self.ElectricityOutputC = self.add_output(self.component_name,
                                                      self.ElectricityOutput,
                                                      lt.LoadTypes.ELECTRICITY,
                                                      lt.Units.WATT)
        
        elif self.fuel == 'hydrogen':
            self.hydrogen_output_c = self.add_output(self.component_name,
                                                     self.HydrogenOutput,
                                                     lt.LoadTypes.HYDROGEN,
                                                     lt.Units.KG_PER_SEC)
        else:
            raise Exception(" The fuel ", str( self.fuel ), " is not available. Choose either 'electricity' or 'hydrogen'. " )
            
        self.add_default_connections( Occupancy, self.get_occupancy_default_connections( ) )
        self.add_default_connections( UtspLpgConnector, self.get_utsp_default_connections( ) )
        self.add_default_connections(controller_l1_generic_runtime.L1GenericRuntimeController, self.get_l1_controller_default_connections())
        
    def get_occupancy_default_connections( self ) -> List[cp.ComponentConnection]:
        log.information("setting occupancy default connections in dhw boiler" )
        connections: List[cp.ComponentConnection] = [ ]
        occupancy_classname = Occupancy.get_classname( )
        connections.append( cp.ComponentConnection( Boiler.WaterConsumption, occupancy_classname, Occupancy.WaterConsumption ) )
        return connections
        
    def get_utsp_default_connections( self ) -> List[cp.ComponentConnection]:
        log.information("setting utsp default connections in dhw boiler" )
        connections: List[cp.ComponentConnection] = [ ]
        occupancy_classname = Occupancy.get_classname( )
        connections.append( cp.ComponentConnection( Boiler.WaterConsumption, occupancy_classname, UtspLpgConnector.WaterConsumption ) )
        return connections
    
    def get_l1_controller_default_connections( self )-> List[cp.ComponentConnection]:
        log.information( "setting l1 default connections in dhw boiler"  )
        connections: List[cp.ComponentConnection] = [ ]
        controller_classname = controller_l1_generic_runtime.L1GenericRuntimeController.get_classname()
        connections.append(cp.ComponentConnection(Boiler.l1_DeviceSignal, controller_classname, controller_l1_generic_runtime.L1GenericRuntimeController.l1_DeviceSignal))
        connections.append(cp.ComponentConnection(Boiler.l1_RunTimeSignal, controller_classname, controller_l1_generic_runtime.L1GenericRuntimeController.l1_RunTimeSignal))
        return connections
    
    @staticmethod
    def get_default_config()-> BoilerConfig:
        config = BoilerConfig( name = 'Boiler',
                               source_weight = 1, 
                               volume = 200,
                               surface = 1.2,
                               u_value = 0.36,
                               T_warmwater = 50,
                               T_drainwater = 10,
                               power = 2400,
                               efficiency = 1,
                               fuel  = 'electricity' )
        return config
    
    def build( self, config : BoilerConfig )-> None:
        
        self.source_weight = config.source_weight
        self.power = config.power
        self.volume = config.volume
        self.surface = config.surface
        self.u = config.u
        self.efficiency = config.efficiency
        self.T_drainwater = config.T_drainwater
        self.T_warmwater = config.T_warmwater
        self.fuel = config.fuel
        
        
        #initialize Boiler State
        self.state = BoilerState( volume_in_l = config.volume, temperature_in_K = 273 + 50 )
        self.previous_state = self.state.clone()
            
        self.write_to_report( )

    def write_to_report(self) -> List[str]:
        lines:List[str] = []
        lines.append("Name: {}".format("electric Boiler"))
        lines.append("Power: {:4.0f} kW".format( ( self.power ) * 1E-3 ) )
        lines.append( "Volume: {:4.0f} l".format( self.volume ) )
        return lines

    def i_save_state( self ) -> None:
        self.previous_state = self.state.clone()

    def i_restore_state( self )  -> None:
        self.state = self.previous_state.clone()

    def i_doublecheck( self, timestep: int, stsv: cp.SingleTimeStepValues )  -> None:
        pass

    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool ) -> None:
        
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
        new_energy = energy - ( self.state.temperature_in_K - 293 ) * self.surface * self.u * self.my_simulation_parameters.seconds_per_timestep * 1e-3 \
                                              - WW_consumption * ( self.T_warmwater - self.T_drainwater ) * 0.977 * 4.182 \
                                              + signal * self.power * self.efficiency * self.my_simulation_parameters.seconds_per_timestep * 1e-3
                                              
        #convert new energy to new temperature
        self.state.set_temperature_from_energy( new_energy )
        
        #save outputs
        stsv.set_output_value( self.TemperatureMeanC, self.state.temperature_in_K - 273.15 )
        
        if self.fuel == 'electricity':
            stsv.set_output_value( self.ElectricityOutputC, self.power * signal )
              
            #put forecast into dictionary
            if self.my_simulation_parameters.predictive_control:
                #only in first timestep
                if self.state.timestep + 1 == timestep:
                    self.state.timestep += 1
                    self.previous_state.timestep += 1
                    runtime:float = stsv.get_input_value( self.l1_RunTimeSignalC )
                    self.simulation_repository.set_dynamic_entry(component_type = lt.ComponentType.ELECTRIC_BOILER,
                                                                 source_weight = self.source_weight, entry =self.power * runtime)
        else:
            #heat of combustion hydrogen: 141.8 MJ / kg; conversion W = J/s to kg / s
            stsv.set_output_value( self.hydrogen_output_c, ( self.power * signal / 1.418 ) * 1e-8 )