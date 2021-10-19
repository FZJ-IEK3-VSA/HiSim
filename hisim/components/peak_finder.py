

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from copy import deepcopy
from dataclasses import dataclass

# Import modules from HiSim
from component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues
from loadtypes import LoadTypes, Units

__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


class ComponentName(Component):
    """
    To document a class, first write a summary of the class that is as accurate as possible.
    It should contain information about what functionalities the class has and what its purpose is in the HiSim project.

    This is an example class. It can be used as a template for creating new component modules.
    Its functionalities serve no further purpose for HiSim.

    Parameters
    ----------
    component_name : str
        Passed to initialize :py:class:`~hisim.component.Component`.

    loadtype : LoadTypes
        A :py:class:`~hisim.loadtypes.LoadTypes` object that represents
        the type of the loaded data.

    unit: LoadTypes.Units
        A :py:class:`~hisim.loadtypes.Units` object that represents
        the unit of the loaded data.

    """

    # Inputs
    PVInput = "PV_Input"
    ElectricityDemandBuilding = "ElectricityDemandBuilding"

    # Outputs
    PickLength = "PickLength"
    PickSize = "PickSize"

    def __init__(self, component_name: str):
        super().__init__(name=component_name)

        # If a component requires states, this can be implemented here.
        self.state = ComponentNameState()

        self.previous_state = deepcopy(self.state)

        self.input_from_other_component: ComponentInput = self.add_input(object_name=self.ComponentName,
                                                                         field_name=self.InputFromOtherComponent,
                                                                         load_type=LoadTypes.Electricity,
                                                                         unit=Units.Watt,
                                                                         mandatory=True)

        self.output_with_state: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                                  field_name=self.OutputWithState,
                                                                  load_type=LoadTypes.Electricity,
                                                                  unit=Units.Wh)

        self.output_without_state: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                                     field_name=self.OutputWithoutState,
                                                                     load_type=LoadTypes.Electricity,
                                                                     unit=Units.Watt)

    def i_save_state(self):
        self.previous_state = deepcopy(self.state)

    def i_restore_state(self):
        self.state = deepcopy(self.previous_state)

    def i_doublecheck(self):
        pass

    def i_simulate(self, stsv: SingleTimeStepValues, seconds_per_timestep : int, force_convergence: bool):
        # define local variables
        input_1 = stsv.get_input_value(self.input_from_other_component)
        input_2 = self.state.output_with_state
        dt = seconds_per_timestep  # TODO: delete after "seconds_per_timestep" is included again

        # do your calculations
        output_1 = input_2 + input_1 * seconds_per_timestep
        output_2 = input_1 + self.factor

        # write values for output time series
        stsv.set_output_value(self.output_with_state, output_1)
        stsv.set_output_value(self.output_without_state, output_2)

        # write values to state
        self.state.output_with_state = output_1


@dataclass
class ComponentNameState:
    """
    This data class saves the state of the simulation results.

    Parameters
    ----------
    output_with_state : int
        Stores the state of the output_with_state value from :py:class:`~hisim.component.ComponentName`.
    """
    output_with_state: int = 0
