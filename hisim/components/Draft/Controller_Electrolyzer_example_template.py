#""Simple implementation of an electrolyzer.
#The Electrolyzer can be turned on and off as you want. It is connected 
#to the EMS like a electricy consumption uncontrolled device 


# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from copy import deepcopy
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Import modules from HiSim
from dataclasses import dataclass

from typing import List

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.components import controller_l1_electrolyzer

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
class StaticElectrolyzerConfig(ConfigBase):

    """Configuration of the ComponentName."""
    #: name of the electrolyer
    name: str
    #: priority of the component in hierachy: the higher the number the lower the priority
    source_weight: int
    #: minimal operating power in Watt (electrical power)
    min_power: float
    #: maximal operating power in Watt (electrical power)
    max_power: float
    #: minimal hydrogen production rate (at minimal electrical power) in kg / s
    min_hydrogen_production_rate: float
    #: maximal hydrogen production rate (at maximal electrical power) in kg / s
    max_hydrogen_production_rate: float

    @staticmethod
    def get_default_config(p_el: float) -> "StaticElectrolyzerConfig":
        """Returns the default configuration of an electrolyzer."""
        config = StaticElectrolyzerConfig(
            name="Electrolyzer",
            source_weight=999,
            min_power=p_el * 0.5,
            max_power=p_el,
            min_hydrogen_production_rate=p_el * (1/4) * 8.989 / 3.6e4,
            max_hydrogen_production_rate=p_el * (50 / 24) * 8.989 / 3.6e4,
            h2_soc_threshold_electrolyzer = 100 # Maximal allowed content of hydrogen storage for turning the electrolyzer on in %
        )
        return config


    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return ComponentName.get_full_classname()

    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    name: str
    loadtype: loadtypes.LoadTypes
    unit: loadtypes.Units

    @classmethod
    def get_default_template_component(cls):
        """Gets a default ComponentName."""
        return StaticElectrolyzerConfig(
            name="StaticElectrolyzer",
            loadtype=loadtypes.LoadTypes.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
            unit=loadtypes.Units.WATT,
        )


class StaticElectrolyzer(cp.Component):

    """Static Electrolyzer

    Static Electrolyzer, which can be manually turned on / off.
    This means, that the Electrolyzer does not depend on surplus Energy available!
    It only depends on if it should produce hydrogen or not.

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
    HydrogenSOC = "HydrogenSOC"
    
    # Outputs
    HydrogenOutput = "HydrogenOutput"

    
    
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: StaticElectrolyzerConfig,
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.componentnameconfig = config
        super().__init__(
            self.componentnameconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
    # Intputs
        self.hydrogen_soc_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HydrogenSOC,
            lt.LoadTypes.HYDROGEN,
            lt.Units.PERCENT,
            mandatory=True,
        )

        self.electricity_target_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            StaticElectrolyzer.Electricity,
            lt.LoadTypes.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
            lt.Units.WATT,
            mandatory=True,
        )

    # Outputs
        self.hydrogen_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            StaticElectrolyzer.HydrogenOutput,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG_PER_SEC,
            output_description="Hydrogen output",
        )


    def i_save_state(self) -> None:
        """Saves the current state."""
        self.previous_state = deepcopy(self.state)

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        
        
        """Simulates the component."""
        # define local variables
        input_1 = stsv.get_input_value(self.HydrogenSOC)
         

        # do your calculations
        if input_1 < h2_soc_threshold_electrolyzer :
            self.state.hydrogen = max_hydrogen_production_rate


        # write values to state
        stsv.set_output_value(self.hydrogen_output_channel, self.state.hydrogen)
        
    def write_to_report(self):
        """Writes the information of the current component to the report."""
    return self.config.get_string_dict()

