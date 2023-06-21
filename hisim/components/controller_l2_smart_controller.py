"""L2 Smart Controller Module."""

# clean
# Generic/Built-in

# Owned
from typing import List, Any, Dict, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.component import Component, SingleTimeStepValues, ConfigBase
from hisim.components.generic_heat_pump import (
    GenericHeatPumpController,
    GenericHeatPumpControllerConfig,
)
from hisim.components.generic_ev_charger import (
    EVChargerController,
    EVChargerControllerConfig,
)
from hisim.simulationparameters import SimulationParameters


# TODO: add more arguments to config
@dataclass_json
@dataclass
class SmartControllerConfig(ConfigBase):

    """Smart Controller Config."""

    name: str

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return SmartController.get_full_classname()

    @classmethod
    def get_default_config_ems(cls) -> Any:
        """Default Config for Energy Management System."""
        config = SmartControllerConfig(
            name=" SmartController",
        )
        return config


class SmartController(Component):

    """Smart Controller class."""

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        controllers: Optional[Dict[str, List[str]]],
        config: SmartControllerConfig,
    ) -> None:
        """Construct all necessary attributes."""
        super().__init__(
            name="SmartController",
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        if controllers is None:
            controllers = {"HeatPump": ["mode"], "EVCharger": ["mode"]}
        self.wrapped_controllers: List[Any] = []
        self.build(controllers)

    def build(self, controllers: Dict[str, List[str]]) -> None:
        """Build wrapped controllers."""
        for controller_name in controllers:
            if "HeatPump" in controller_name:
                ghpcc = GenericHeatPumpControllerConfig(
                    "generic heat pump controller",
                    temperature_air_heating_in_celsius=15,
                    temperature_air_cooling_in_celsius=25,
                    offset=0,
                    mode=1,
                )
                self.wrapped_controllers.append(
                    GenericHeatPumpController(
                        my_simulation_parameters=self.my_simulation_parameters,
                        config=ghpcc,
                    )
                )

            elif "EVCharger" in controller_name:
                self.wrapped_controllers.append(
                    EVChargerController(
                        my_simulation_parameters=self.my_simulation_parameters,
                        config=EVChargerControllerConfig.get_default_config(),
                    )
                )

        self.add_io()

    def connect_similar_inputs(self, components: List[Any]) -> None:
        """Connect similar inputs."""
        if len(self.inputs) == 0:
            raise Exception("The component " + self.component_name + " has no inputs.")

        if isinstance(components, list) is False:
            components = [components]

        for component in components:
            if isinstance(component, Component) is False:
                raise Exception("Input variable is not a component")
            has_not_been_connected = True
            index = None
            for index, _ in enumerate(self.wrapped_controllers):
                for input_channel in self.wrapped_controllers[index].inputs:
                    for output in component.outputs:
                        if input_channel.field_name == output.field_name:
                            has_not_been_connected = False
                            self.wrapped_controllers[index].connect_input(
                                self.wrapped_controllers[index].field_name,
                                component.component_name,
                                output.field_name,
                            )
            if has_not_been_connected and index is not None:
                raise Exception(
                    f"No similar inputs from {self.wrapped_controllers[index].component_name} are compatible with the outputs of {component.component_name}!"
                )

    def add_io(self) -> None:
        """Add inputs and outputs."""
        for controller in self.wrapped_controllers:
            for input_channel in controller.inputs:
                self.inputs.append(input_channel)
            for output in controller.outputs:
                self.outputs.append(output)

    def i_save_state(self) -> None:
        """Save the current state."""
        for index, _ in enumerate(self.wrapped_controllers):
            self.wrapped_controllers[index].i_save_state()

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        for index, _ in enumerate(self.wrapped_controllers):
            self.wrapped_controllers[index].i_restore_state()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the Smart Controller class."""
        for index, _ in enumerate(self.wrapped_controllers):
            self.wrapped_controllers[index].i_simulate(
                timestep=timestep, stsv=stsv, force_convergence=force_convergence
            )

    def connect_electricity(self, component: Any) -> None:
        """Connect Electricity input."""
        for index, _ in enumerate(self.wrapped_controllers):
            if hasattr(self.wrapped_controllers[index], "ElectricityInput"):
                if isinstance(component, Component) is False:
                    raise Exception("Input has to be a component!")
                if hasattr(component, "ElectricityOutput") is False:
                    raise Exception("Input Component does not have Electricity Output!")
                self.connect_input(
                    self.wrapped_controllers[index].ELECTRICITY_INPUT,
                    component.component_name,
                    component.ElectricityOutput,
                )
