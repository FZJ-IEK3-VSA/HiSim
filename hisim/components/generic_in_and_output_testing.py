# Owned
from hisim.component import Component
from hisim import loadtypes as lt
from dataclasses import dataclass
import hisim.component as cp
from typing import List
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.components.advanced_heat_pump_hplib import HeatPumpHplib

__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = ""


@dataclass
class DynamicConnectionInput:
    SourceComponentClass: str
    SourceComponentOutput: cp.ComponentOutput
    SourceLoadType: lt
    SourceUnit: lt
    SourceTags: list
    SourceWeight: int
@dataclass
class DynamicConnectionOutput:
    SourceOutputName: str
    SourceTags:list
    SourceLoadType: lt
    SourceUnit: lt

class Test_InandOutputs(Component):
    """

    """
    #Input
    MyComponentInputs :List[DynamicConnectionInput] = []
    TempInput = "TempInput"
    #Output
    MyComponentOutputs: List[DynamicConnectionInput] = []
    MassflowOutput= "Hot Water Energy Output"

    def __init__(self,my_simulation_parameters: SimulationParameters ):
        super().__init__(name="Test_InandOutputs", my_simulation_parameters=my_simulation_parameters)

        self.temp_input: cp.ComponentInput = self.add_input(self.ComponentName, self.TempInput,
                                                                  lt.LoadTypes.Water, lt.Units.kg_per_sec, True)
        self.mass_out: cp.ComponentOutput = self.add_output(self.ComponentName, Test_InandOutputs.MassflowOutput, lt.LoadTypes.Water, lt.Units.kg_per_sec)

    def add_component_input_and_connect(self,
                            source_component_class: cp.Component,
                            source_component_output:cp.ComponentOutput,
                            source_load_type:lt,
                            source_unit:lt,
                            source_tags:list,
                            source_weight:int):

        # Label Input and generate variable
        num_inputs = len(self.inputs)
        label = "Input{}".format(num_inputs)
        vars(self)[label] = label

        # Define Input as Component Input and add it to inputs
        myinput = cp.ComponentInput(self.ComponentName, label, source_load_type, source_unit, True)
        self.inputs.append(myinput)
        myinput.src_object_name = source_component_class.ComponentName
        myinput.src_field_name = source_component_output
        self.__setattr__(label,myinput)

        # Connect Input and define it as DynamicConnectionInput
        for output_var in source_component_class.outputs:
            if output_var.ObjectName == source_component_class.ComponentName:
                self.connect_input( label,
                                    source_component_class.ComponentName,
                                    output_var.FieldName)
                self.MyComponentInputs.append(DynamicConnectionInput(SourceComponentClass=label,
                                                                     SourceComponentOutput=source_component_output,
                                                                     SourceLoadType=source_load_type,
                                                                     SourceUnit=source_unit,
                                                                     SourceTags=source_tags,
                                                                     SourceWeight=source_weight))

    def add_component_output(self, source_output_name: str,source_tags: list,source_load_type:lt,source_unit:lt):

        # Label Output and generate variable
        num_inputs = len(self.outputs)
        label = "Output{}".format(num_inputs + 1)
        vars(self)[label] = label

        # Define Output as Component Input and add it to inputs
        myoutput = cp.ComponentOutput(self.ComponentName, source_output_name+label, source_load_type, source_unit, True)
        self.outputs.append(myoutput)
        self.__setattr__(label,myoutput)

        # Define Output as DynamicConnectionInput
        self.MyComponentOutputs.append(DynamicConnectionOutput(SourceOutputName=source_output_name+label,
                                                              SourceTags=source_tags,
                                                              SourceLoadType=source_load_type,
                                                              SourceUnit=source_unit))
        return myoutput
    def get_heat_pump_default_connections(self):
        log.information("setting weather default connections")
        connections = []
        heat_pump_classname = HeatPumpHplib.get_classname()
        connections.append(cp.ComponentConnection(Test_InandOutputs.TemperatureOutside,
                                                  heat_pump_classname,
                                                  HeatPumpHplib.ThermalOutputPower))

    def write_to_report(self):
        pass

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass
    def rule_heaters(self,heat_demand:float,stsv:cp.SingleTimeStepValues):
        weight_counter = 1
        heat_produced=0
        MyComponentInputs=self.MyComponentInputs
        while len(MyComponentInputs) != 0:
            for index, element in enumerate(MyComponentInputs):
                for tags in element.SourceTags:
                    if tags.__class__ == lt.ComponentType and tags.title() in lt.ComponentType.Heaters.title():
                        if element.SourceWeight == weight_counter:
                            heat_produced=heat_produced+stsv.get_input_value(self.__getattribute__(element.SourceComponentClass))
                            MyComponentInputs.remove(element)
                            if heat_demand<heat_produced:
                                return weight_counter, heat_produced
                            else:
                                weight_counter = weight_counter + 1
                    else:
                        MyComponentInputs.remove(element)
        return weight_counter, heat_produced


    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
        heat_demand=100
        weight_counter, heat_produced =self.rule_heaters(heat_demand=heat_demand,stsv=stsv)

        '''
        # Sollen die connections nur über default gesetzt werden oder auch "manuell"?
         -> im Moment ist manuell
        # TO-DO: Eine DataClass für Entry and Exit?
        # Weight Priority nennen
        
        In Data Class muss nicht Output Type definiert sein, kann gelöscht werden
        
        ad_componen_input to add_component_input_and_connect
        
        neben connect_input connect_dynamic_input hinzufügen, behinhaltet.
        - Name des INputs der verbindet werden soll
        - output1 objekt
        '''




