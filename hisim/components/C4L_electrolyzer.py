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
from hisim.components import (controller_C4L_electrolyzer_fuelcell_1a_1b,controller_predicitve_C4L_electrolyzer_fuelcell, controller_C4L_electrolyzer)
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
class C4LElectrolyzerConfig(cp.ConfigBase):

    name: str
    loadtype: loadtypes.LoadTypes
    unit: loadtypes.Units
    p_el: float #Electricity consumption of Electrolyzer if electrolyzer is in operating, in Watt
    output_description: str
    p_el_percentage_standby_electrolyzer: int #if electrolyzer runs in standby, than it needs "p_el_percentage_standby_electrolyzer" (%) electricity power of the electrolyzer operating power 
    
    @staticmethod
    def get_default_config() -> "C4LElectrolyzerConfig":
        """Returns the default configuration of an electrolyzer."""
        config = C4LElectrolyzerConfig(
            name="C4LElectrolyzer",
            p_el = 0,
            loadtype=loadtypes.LoadTypes.HEATING,
            unit=loadtypes.Units.WATT,
            output_description = "Electrolyzer E-Consumption",
            p_el_percentage_standby_electrolyzer = 0,

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

#Neu --> gehört wsl. in den electrolyzer controller!!
# class C4LElectrolyzerState:
#     """Data class that saves the state of the C4L Electrolyzer."""
#     def __init__(
#         self,
#         on_off: int,
#     ) -> None:
#         """Initializes C4L Electrolyzer state.
#     :0 if turned off
#         """
#         self.on_off:int = on_off
    
    

#     def clone(self) -> "StaticElectrolyzerState":
#         """Copies the current instance."""
#         return StaticElectrolyzerState(
#             on_off=self.on_off,
#         )

#i_restore_state  --> Anschauen!!!! 

class C4LElectrolyzerState:
    """This data class saves the state of the CHP."""

    def __init__(self, state: int) -> None:
        self.state = state

    def clone(self) -> "C4LElectrolyzerState":
        return C4LElectrolyzerState(state=self.state)

class C4LElectrolyzer(cp.Component):

    """C4LElectrolyzer

    C4LElectrolyzer, which can be manually turned on / off.
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
    # Inputs
    ElectrolyzerControllerOnOffSignal = "ElectrolyzerControllerOnOffSignal"
    # Outputs
    ElectricityConsumption: str = "ElectricityConsumptionElectrolyzer"
    HydrogenOutput: str = "HydrogenOutput" #Hydrogen Output in kg per sec
    
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: C4LElectrolyzerConfig,
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.C4LElectrolyzerConfig = config
        super().__init__(
            self.C4LElectrolyzerConfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,

        )
        
        self.state: C4LElectrolyzerState = C4LElectrolyzerState(0)
        self.previous_state: C4LElectrolyzerState = self.state.clone()
        self.processed_state: C4LElectrolyzerState = self.state.clone()


    # Outputs
        
        self.output_needed_electricity: ComponentOutput = self.add_output(
            object_name=self.C4LElectrolyzerConfig.name,
            field_name=self.ElectricityConsumption,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
            output_description="Output with State",
        )

        self.hydrogen_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.HydrogenOutput,
            load_type=loadtypes.LoadTypes.HYDROGEN,
            unit=loadtypes.Units.KG_PER_SEC,
            output_description="Hydrogen output",
        )
        
        #self.add_default_connections(self.get_default_connections_from_h2_storage())
        self.add_default_connections(self.get_default_connections_from_controller_C4L_electrolyzer())
        self.add_default_connections(self.get_default_connections_from_electrolyzerfuelcell_controller())
        self.add_default_connections(self.get_default_connections_from_electrolyzerfuelcell_nonpredictivecontroller())

        # Inputs
        self.electrolyzer_onoff_signal_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectrolyzerControllerOnOffSignal,
            loadtypes.LoadTypes.ON_OFF,
            loadtypes.Units.BINARY,
            mandatory=True,
        )

    def get_default_connections_from_controller_C4L_electrolyzer(self) -> List[cp.ComponentConnection]:
        log.information("setting fuel cell default connections in generic H2 storage")
        connections: List[cp.ComponentConnection] = []
        electrolyzer_classname = controller_C4L_electrolyzer.C4LelectrolyzerController.get_classname()
        connections.append(
            cp.ComponentConnection(

                C4LElectrolyzer.ElectrolyzerControllerOnOffSignal ,
                electrolyzer_classname,
                controller_C4L_electrolyzer.C4LelectrolyzerController.ElectrolyzerControllerOnOffSignal,
            )
        )
        return connections
    
    def get_default_connections_from_electrolyzerfuelcell_nonpredictivecontroller(self) -> List[cp.ComponentConnection]:
        log.information("setting fuel cell default connections in generic H2 storage")
        connections: List[cp.ComponentConnection] = []
        electrolyzer_classname = controller_C4L_electrolyzer_fuelcell_1a_1b.C4Lelectrolyzerfuelcell1a1bController.get_classname()
        connections.append(
            cp.ComponentConnection(

                C4LElectrolyzer.ElectrolyzerControllerOnOffSignal ,
                electrolyzer_classname,
                controller_C4L_electrolyzer_fuelcell_1a_1b.C4Lelectrolyzerfuelcell1a1bController.ElectrolyzerControllerOnOffSignal,
            )
        )
        return connections
    
    def get_default_connections_from_electrolyzerfuelcell_controller(self) -> List[cp.ComponentConnection]:
        log.information("setting fuel cell default connections in generic H2 storage")
        connections: List[cp.ComponentConnection] = []
        electrolyzer_classname = controller_predicitve_C4L_electrolyzer_fuelcell.C4LelectrolyzerfuelcellpredictiveController.get_classname()
        connections.append(
            cp.ComponentConnection(

                C4LElectrolyzer.ElectrolyzerControllerOnOffSignal ,
                electrolyzer_classname,
                controller_predicitve_C4L_electrolyzer_fuelcell.C4LelectrolyzerfuelcellpredictiveController.ElectrolyzerControllerOnOffSignal,
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
        #Inputs 
        self.state.state = int(stsv.get_input_value(self.electrolyzer_onoff_signal_channel))

        #Neu if
        if force_convergence:
            # states are saved after each timestep, outputs after each iteration
            # outputs have to be in line with states, so if convergence is forced outputs are aligned to last known state.
            self.state = self.processed_state.clone()

        else:
            """Simulates the component."""
            #Electrolyzer is running, is turned off, or is in standby:

            if self.state.state ==  0 or self.state.state == 1:
                #Running or turned off
                hydrogen_production = self.config.p_el / (3600*40000) #umrechnung von Watt [=Joule/Sekunde, Leistung) p_el in  kg/s H2
                stsv.set_output_value(self.hydrogen_output_channel, self.state.state * hydrogen_production) 
                stsv.set_output_value(self.output_needed_electricity, self.state.state * self.config.p_el)
                self.processed_state = self.state.clone() #Neu
            elif self.state.state == 99: #Standby Electrolyzer
                #Running in standby, so electrolyzer is NOT producing hydrogen
                hydrogen_production = 0
                stsv.set_output_value(self.hydrogen_output_channel,  hydrogen_production) 
                stsv.set_output_value(self.output_needed_electricity,  self.config.p_el*self.config.p_el_percentage_standby_electrolyzer/100)
                self.processed_state = self.state.clone() #Neu

        # write values to state

    def write_to_report(self):
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()