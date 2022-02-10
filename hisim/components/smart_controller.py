# Generic/Built-in

# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim.components.heat_pump import HeatPumpController
from hisim.components.ev_charger import EVChargerController
from typing import List
from hisim import loadtypes as lt
from abc import ABC, abstractmethod
from hisim.simulationparameters import SimulationParameters
# NOT BEING ANYWHERE

class SmartController(Component):

    def __init__(self,my_simulation_parameters: SimulationParameters
,controllers: dict = {"HeatPump":["mode"], "EVCharger":["mode"]}):
        super().__init__(name="SmartController", my_simulation_parameters=my_simulation_parameters)
        self.WrappedControllers:List  = []
        self.build(controllers)

    def build(self, controllers):
        for controller_name in controllers:
            if "HeatPump" in controller_name:
                self.WrappedControllers.append(HeatPumpController(my_simulation_parameters=self.my_simulation_parameters))
            elif "EVCharger" in controller_name:
                self.WrappedControllers.append(EVChargerController(my_simulation_parameters=self.my_simulation_parameters))
        self.add_io()

    def connect_similar_inputs(self, components):
        if len(self.inputs) == 0:
            raise Exception("The component " + self.ComponentName + " has no inputs.")

        if isinstance(components, list) is False:
            components = [components]

        for component in components:
            if isinstance(component, Component) is False:
                raise Exception("Input variable is not a component")
            has_not_been_connected = True
            for index, controller in enumerate(self.WrappedControllers):
                for input in self.WrappedControllers[index].inputs:
                        for output in component.outputs:
                            if input.FieldName == output.FieldName:
                                has_not_been_connected = False
                                self.WrappedControllers[index].connect_input(self.WrappedControllers[index].FieldName, component.ComponentName, output.FieldName)
            if has_not_been_connected:
                raise Exception("No similar inputs from {} are compatible with the outputs of {}!".format(self.WrappedControllers[index].ComponentName, component.ComponentName) )

    def add_io(self):
        for controller in self.WrappedControllers:
            for input in controller.inputs:
                self.inputs.append(input)
            for output in controller.outputs:
                self.outputs.append(output)

    def i_save_state(self):
        for index, controller in enumerate(self.WrappedControllers):
            self.WrappedControllers[index].i_save_state()

    def i_restore_state(self):
        for index, controller in enumerate(self.WrappedControllers):
            self.WrappedControllers[index].i_restore_state()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        for index, controller in enumerate(self.WrappedControllers):
            self.WrappedControllers[index].i_simulate(timestep=timestep, stsv=stsv, force_convergence=force_convergence)

    def connect_electricity(self, component):
        for index, controller in enumerate(self.WrappedControllers):
            if hasattr(self.WrappedControllers[index], "ElectricityInput"):
                if isinstance(component, Component) is False:
                    raise Exception("Input has to be a component!")
                elif hasattr(component, "ElectricityOutput") is False:
                    raise Exception("Input Component does not have Electricity Output!")
                self.connect_input(self.WrappedControllers[index].ElectricityInput, component.ComponentName, component.ElectricityOutput)

