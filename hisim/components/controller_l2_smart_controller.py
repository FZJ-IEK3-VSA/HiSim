"""L2 Smart Controller Module."""

# Generic/Built-in

# Owned
from typing import List, Union, Dict, Optional, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.component import Component, SingleTimeStepValues, ConfigBase, DisplayConfig
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

    building_name: str
    name: str

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the full class name of the base class."""
        return SmartController.get_full_classname()

    @classmethod
    def get_default_config_ems(
        cls,
        building_name: str = "BUI1",
    ) -> "SmartControllerConfig":
        """Default Config for Energy Management System."""
        config = SmartControllerConfig(
            building_name=building_name,
            name="SmartController",
        )
        return config


class SmartController(Component):
    """Smart Controller class."""

    my_simulation_parameters: SimulationParameters
    config: SmartControllerConfig

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        controllers: Optional[Dict[str, List[str]]],
        config: SmartControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
        wrapped_controllers: Optional[List[Any]] = None,
    ) -> None:
        """Construct all necessary attributes.

        ``wrapped_controllers`` is an optional seam for tests: when it is
        provided, the internal construction of the wrapped heat-pump and
        EV-charger controllers in :meth:`build` is skipped and the given
        (lightweight) controllers are used directly. When it is ``None``
        (the default) the original behaviour is preserved.
        """
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        if controllers is None and wrapped_controllers is None:
            controllers = {"HeatPump": ["mode"], "EVCharger": ["mode"]}
        self.wrapped_controllers: List[Any] = []
        self.build(controllers, wrapped_controllers=wrapped_controllers)

    def build(
        self,
        controllers: Optional[Dict[str, List[str]]],
        wrapped_controllers: Optional[List[Any]] = None,
    ) -> None:
        """Build wrapped controllers.

        When ``wrapped_controllers`` is provided, the internal construction of
        the heat-pump and EV-charger controllers is skipped and the injected
        controllers are used instead. This keeps the default behaviour
        unchanged while allowing tests to pass in lightweight fakes.
        """
        if wrapped_controllers is not None:
            self.wrapped_controllers = list(wrapped_controllers)
            self.add_io()
            return
        if controllers is None:
            controllers = {"HeatPump": ["mode"], "EVCharger": ["mode"]}
        for controller_name in controllers:
            if "HeatPump" in controller_name:
                ghpcc = GenericHeatPumpControllerConfig(
                    building_name=self.config.building_name,
                    name="generic heat pump controller",
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

    def connect_similar_inputs(self, components: Union[List[Component], Component]) -> None:
        """Connect similar inputs."""
        if len(self.inputs) == 0:
            raise ValueError("The component " + self.component_name + " has no inputs.")

        if not isinstance(components, list):
            components = [components]

        for component in components:
            if isinstance(component, Component) is False:
                raise TypeError("Input variable is not a component")
            has_not_been_connected = True
            index: Optional[int] = None
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
                raise ValueError(
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

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the Smart Controller class."""
        for index, _ in enumerate(self.wrapped_controllers):
            self.wrapped_controllers[index].i_simulate(
                timestep=timestep, stsv=stsv, force_convergence=force_convergence
            )

    def connect_electricity(self, component: Component) -> None:
        """Connect Electricity input."""
        for index, _ in enumerate(self.wrapped_controllers):
            if hasattr(self.wrapped_controllers[index], "ElectricityInput"):
                if not isinstance(component, Component):
                    raise TypeError("Input has to be a component!")
                if not hasattr(component, "ElectricityOutput"):
                    raise AttributeError("Input Component does not have Electricity Output!")
                self.connect_input(
                    self.wrapped_controllers[index].ELECTRICITY_INPUT,
                    component.component_name,
                    component.ElectricityOutput,
                )
