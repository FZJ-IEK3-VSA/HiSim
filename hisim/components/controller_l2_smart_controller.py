# Generic/Built-in

# Owned
from hisim.component import Component, SingleTimeStepValues
from hisim.components.generic_heat_pump  import HeatPumpController
from hisim.components.generic_ev_charger  import EVChargerController
from typing import List, Any, Dict
from hisim.simulationparameters import SimulationParameters


class SmartController(Component):

    def __init__(self, my_simulation_parameters: SimulationParameters, controllers: Dict = {"HeatPump":["mode"], "EVCharger":["mode"]})  -> None:
        super().__init__(name="SmartController", my_simulation_parameters=my_simulation_parameters)
        self.WrappedControllers:List[Any] = []
        self.build(controllers)

    def build(self, controllers:Dict[Any,Any]) -> None:
        for controller_name in controllers:
            if "HeatPump" in controller_name:
                self.WrappedControllers.append(HeatPumpController(my_simulation_parameters=self.my_simulation_parameters))
            elif "EVCharger" in controller_name:
                self.WrappedControllers.append(EVChargerController(my_simulation_parameters=self.my_simulation_parameters))
        self.add_io()

    def connect_similar_inputs(self, components:List[Any]) -> None:
        if len(self.inputs) == 0:
            raise Exception("The component " + self.component_name + " has no inputs.")

        if isinstance(components, list) is False:
            components = [components]

        for component in components:
            if isinstance(component, Component) is False:
                raise Exception("Input variable is not a component")
            has_not_been_connected = True
            index = None
            for index, _ in enumerate(self.WrappedControllers):
                for input_channel in self.WrappedControllers[index].inputs:
                    for output in component.outputs:
                        if input_channel.field_name == output.field_name:
                            has_not_been_connected = False
                            self.WrappedControllers[index].connect_input(self.WrappedControllers[index].field_name, component.component_name, output.field_name)
            if has_not_been_connected and index is not None:
                raise Exception("No similar inputs from {} are compatible with the outputs of {}!".format(self.WrappedControllers[index].component_name, component.component_name))

    def add_io(self) -> None:
        for controller in self.WrappedControllers:
            for input_channel in controller.inputs:
                self.inputs.append(input_channel)
            for output in controller.outputs:
                self.outputs.append(output)

    def i_save_state(self) -> None:
        for index, _ in enumerate(self.WrappedControllers):
            self.WrappedControllers[index].i_save_state()

    def i_restore_state(self) -> None:
        for index, _ in enumerate(self.WrappedControllers):
            self.WrappedControllers[index].i_restore_state()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        for index, _ in enumerate(self.WrappedControllers):
            self.WrappedControllers[index].i_simulate(timestep=timestep, stsv=stsv, force_convergence=force_convergence)

    def connect_electricity(self, component: Any) -> None:
        for index, _ in enumerate(self.WrappedControllers):
            if hasattr(self.WrappedControllers[index], "ElectricityInput"):
                if isinstance(component, Component) is False:
                    raise Exception("Input has to be a component!")
                if hasattr(component, "ElectricityOutput") is False:
                    raise Exception("Input Component does not have Electricity Output!")
                self.connect_input(self.WrappedControllers[index].ELECTRICITY_INPUT, component.component_name,
                                   component.ElectricityOutput)
