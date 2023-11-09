#""Simple implementation of an electrolyzer.
#The Electrolyzer can be turned on and off as you want. It is connected 
#to the EMS like a electricy consumption uncontrolled device  Base:example_template.py"


# clean

# # Import packages from standard library or the environment e.g. pandas, numpy etc.
# from copy import deepcopy
# from dataclasses import dataclass
# from dataclasses_json import dataclass_json

# # Import modules from HiSim
# from dataclasses import dataclass

# from typing import List

# from dataclasses_json import dataclass_json

# from hisim import component as cp
# from hisim import loadtypes as lt
# from hisim import log
# from hisim.simulationparameters import SimulationParameters
# from hisim.components import controller_l1_electrolyzer
# from hisim.component import ConfigBase

from typing import List
import pandas as pd
import os
from hisim import log
from hisim.components import generic_hydrogen_storage
from hisim import loadtypes
from hisim import utils
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from copy import deepcopy

__authors__ = "CB"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "CB"
__email__ = "christof.bernsteiner@4wardenergy.at"
__status__ = ""




@dataclass_json
@dataclass
class StaticElectrolyzerConfig(cp.ConfigBase):

    name: str
    loadtype: loadtypes.LoadTypes
    unit: loadtypes.Units
    p_el: float #Electricity consumption of Electrolyzer in Watt
    output_description: str
    on_off_SOEC: int
    off_on_SOEC: int
    h2_soc_upper_threshold_electrolyzer: int
    
    @staticmethod
    def get_default_config() -> "StaticElectrolyzerConfig":
        """Returns the default configuration of an electrolyzer."""
        config = StaticElectrolyzerConfig(
            name="StaticElectrolyzer",
            p_el = 0,
            loadtype=loadtypes.LoadTypes.HEATING,
            unit=loadtypes.Units.WATT,
            output_description = "Electrolyzer E-Consumption",
            on_off_SOEC = 0,  #Tag Abschalten des Elektrolyseurs und dafür Einschalten der Brennstoffzelle
            off_on_SOEC = 0,  #Tag Einschalten des Elektrolyseurs und dafür Ausschalten der Brennstoffzelle
            h2_soc_upper_threshold_electrolyzer = 100,
        )
        return config


    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return ComponentName.get_full_classname()

'''
    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    component_name: str
    loadtype: loadtypes.LoadTypes
    unit: loadtypes.Units
'''

#Neu
class StaticElectrolyzerState:
    """Data class that saves the state of the CHP controller."""
    def __init__(
        self,
        on_off: int,
    ) -> None:
        """Initializes CHP Controller state.
    :0 if turned off
        """
        self.on_off:int = on_off
    
    

    def clone(self) -> "StaticElectrolyzerState":
        """Copies the current instance."""
        return StaticElectrolyzerState(
            on_off=self.on_off,
        )

#i_restore_state  --> Anschauen!!!! 



class StaticElectrolyzer(cp.Component):

    """Static Electrolyzer

    Static Electrolyzer, which can be manually turned on / off.
    This means, that the Electrolyzer does not depend on surplus Energy available!
    It only depends on if it is forced to produce hydrogen or not.

    Parameters
    ----------
    component_name : str
        Passed to initialize :py:class:`~hisim.component.Component`.

    loadtype : LoadType
        A :py:class:`~hisim.loadtypes.LoadTypes` object that represents
        the type of the loaded data.

    unit: LoadTypes.Units
        A :py:class:`~hisim.loadtypes.Units` object that represents
        the unit of the loaded data.

    """
 
    HydrogenSOC = "HydrogenSOC"
    # Outputs
    ElectricityConsumption: str = "ElectricityConsumptionElectrolyzer"
    HydrogenOutput: str = "HydrogenOutput" #Hydrogen Output in kg per sec
    
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: StaticElectrolyzerConfig,
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.StaticElectrolyzerConfig = config
        super().__init__(
            self.StaticElectrolyzerConfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,

        )
        
        self.state: StaticElectrolyzerState = StaticElectrolyzerState(0)
        self.previous_state: StaticElectrolyzerState = self.state.clone()
        self.processed_state: StaticElectrolyzerState = self.state.clone()

        self.hydrogen_soc_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HydrogenSOC,
            loadtypes.LoadTypes.HYDROGEN,
            loadtypes.Units.PERCENT,
            mandatory=True,
        )

    # Outputs
        
        self.output_needed_electricity: ComponentOutput = self.add_output(
            object_name=self.StaticElectrolyzerConfig.name,
            field_name=self.ElectricityConsumption,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
            output_description="Output with State",
        )

        self.hydrogen_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            StaticElectrolyzer.HydrogenOutput,
            loadtypes.LoadTypes.HYDROGEN,
            loadtypes.Units.KG_PER_SEC,
            output_description="Hydrogen output",
        )
        
        self.add_default_connections(self.get_default_connections_from_h2_storage())

    def get_default_connections_from_h2_storage(self):
        """Sets default connections for the hydrogen storage in the electrolyzer controller."""
        log.information("setting hydrogen storage default connections in Electrolyzer Controller")
        connections = []
        h2_storage_classname = generic_hydrogen_storage.GenericHydrogenStorage.get_classname()
        connections.append(
            cp.ComponentConnection(
                StaticElectrolyzer.HydrogenSOC,
                h2_storage_classname,
                generic_hydrogen_storage.GenericHydrogenStorage.HydrogenSOC,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass
    
    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone()


    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        
        #Neu if
        if force_convergence:
            # states are saved after each timestep, outputs after each iteration
            # outputs have to be in line with states, so if convergence is forced outputs are aligned to last known state.
            self.state = self.processed_state.clone()
            print("Noch etwas zu tun-Static Electrolyzer/Hydrogen Storage Size")
        else:
            """Simulates the component."""
            # define local variables

            
            if self.hydrogen_soc_channel.source_output is not None:
                hydrogen_soc = stsv.get_input_value(self.hydrogen_soc_channel)
                hydrogen_soc_ok = hydrogen_soc >= self.config.h2_soc_upper_threshold_electrolyzer

            
            else:
                hydrogen_soc_ok = False

            
            if not hydrogen_soc_ok: 
                calc_electrolyzer = ((timestep >= self.config.on_off_SOEC*24) and (timestep <= self.config.off_on_SOEC*24))        
                
                if calc_electrolyzer:
                    stsv.set_output_value(self.hydrogen_output_channel, 0)
                    stsv.set_output_value(self.output_needed_electricity, 0)
                else:
                    hydrogen_production = self.config.p_el / (3600*40000)
                    stsv.set_output_value(self.hydrogen_output_channel, hydrogen_production) #umrechnung von Watt [=Joule/Sekunde, Leistung) p_el in  kg/s H2
                    stsv.set_output_value(self.output_needed_electricity, self.config.p_el)
            
            self.processed_state = self.state.clone() #Neu
              
                

        # write values to state

    def write_to_report(self):
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()