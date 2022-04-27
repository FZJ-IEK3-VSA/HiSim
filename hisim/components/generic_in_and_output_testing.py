# Owned
from hisim.component import Component
from hisim import loadtypes as lt
from dataclasses import dataclass
import hisim.component as cp
from typing import List
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
__authors__ = "Frank Burkrad, Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = ""

@dataclass
class DynamicConnectionEntry:
    SourceComponentClass: cp.Component
    SourceComponentOutput: cp.ComponentOutput
    SourceLoadType: lt
    SourceUnit: lt

class Test_InandOutputs(Component):
    """

    """
    #Input
    MyComponentInputs :List[DynamicConnectionEntry] = []
    TempInput = "TempInput"
    #Output
    MassflowOutput= "Hot Water Energy Output"

    def __init__(self,my_simulation_parameters: SimulationParameters ):
        super().__init__(name="Test_InandOutputs", my_simulation_parameters=my_simulation_parameters)

        self.temp_input: cp.ComponentInput = self.add_input(self.ComponentName, self.TempInput,
                                                                  lt.LoadTypes.Water, lt.Units.kg_per_sec, True)
        self.mass_out: cp.ComponentOutput = self.add_output(self.ComponentName, Test_InandOutputs.MassflowOutput, lt.LoadTypes.Water, lt.Units.kg_per_sec)

    def add_component_input(self, source_component_class: cp.Component,source_component_output:cp.ComponentOutput, source_load_type:lt, source_unit:lt):
        self.MyComponentInputs.append(DynamicConnectionEntry(source_component_class,source_component_output,source_load_type,source_unit))
        num_inputs = len(self.inputs)
        label = "Input{}".format(num_inputs + 1)
        vars(self)[label] = label
        myinput = cp.ComponentInput(self.ComponentName, label, source_load_type, source_unit, True)
        self.inputs.append(myinput)
        myinput.src_object_name = source_component_class.ComponentName
        myinput.src_field_name = source_component_output
        self.__setattr__(label,myinput)

        for output_var in source_component_class.outputs:
            if output_var.ObjectName == source_component_class.ComponentName:
                self.connect_input( label,
                                    source_component_class.ComponentName,
                                    output_var.FieldName)

    def write_to_report(self):
        pass

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):

        print(stsv.get_input_value(self.Input2)+stsv.get_input_value(self.Input3))
