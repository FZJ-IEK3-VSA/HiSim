"""L2 Smart Controller Module."""

# Generic/Built-in

# Owned
from typing import List, Union, Dict, Optional, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.component import Component, SingleTimeStepValues
from hisim.config import ConfigBase, ComponentID, DisplayConfig
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

    component_id: ComponentID

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the full class name of the base class."""
        return SmartController.get_full_classname()

    @classmethod
    def get_default_config_ems(
        cls,
        component_id: Optional[ComponentID] = None,
    ) -> "SmartControllerConfig":
        """Default Config for Energy Management System."""
        if component_id is None:
            component_id = ComponentID(name="SmartController")
        config = SmartControllerConfig(
            component_id=component_id,
        )
        return config


class SmartController(Component):
    """Smart Controller class."""

    my_simulation_parameters: SimulationParameters
    config: SmartControllerConfig

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        controller_type_to_field_names: Optional[Dict[str, List[str]]],
        config: SmartControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
        wrapped_controllers: Optional[List[Any]] = None,
    ) -> None:
        """Construct the SmartController and its wrapped controllers.

        Args:
            my_simulation_parameters: Simulation parameters for this run.
            controller_type_to_field_names: Mapping of controller-type names to lists of field
                names. If ``None`` and ``wrapped_controllers`` is also ``None``,
                defaults to {"HeatPump": ["mode"], "EVCharger": ["mode"]}.
            config: SmartController configuration (building name, display name).
            my_display_config: Display configuration; defaults to DisplayConfig().
            wrapped_controllers: Optional seam for tests — when provided, internal
                construction of wrapped controllers is skipped and the given
                controllers are used directly.
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
        if controller_type_to_field_names is None and wrapped_controllers is None:
            controller_type_to_field_names = {"HeatPump": ["mode"], "EVCharger": ["mode"]}
        self.wrapped_controllers: List[Any] = []
        self.build_wrapped_controllers(controller_type_to_field_names, wrapped_controllers=wrapped_controllers)

    def build_wrapped_controllers(
        self,
        controller_type_to_field_names: Optional[Dict[str, List[str]]],
        wrapped_controllers: Optional[List[Any]] = None,
    ) -> None:
        """Build wrapped controllers.

        When ``wrapped_controllers`` is provided, the internal construction of
        the heat-pump and EV-charger controllers is skipped and the injected
        controllers are used instead. This keeps the default behaviour
        unchanged while allowing tests to pass in lightweight fakes.

        ``controller_type_to_field_names`` maps controller-type names (e.g. ``"HeatPump"``,
        ``"EVCharger"``) to lists of field names; the constructed controller
        objects are stored in ``self.wrapped_controllers``.
        """
        if wrapped_controllers is not None:
            self.wrapped_controllers = list(wrapped_controllers)
            self.add_wrapped_controller_inputs_and_outputs()
            return
        if controller_type_to_field_names is None:
            controller_type_to_field_names = {"HeatPump": ["mode"], "EVCharger": ["mode"]}
        for controller_name in controller_type_to_field_names:
            if "HeatPump" in controller_name:
                heat_pump_config = GenericHeatPumpControllerConfig(
                    component_id=ComponentID(
                        name="generic heat pump controller", building=self.config.component_id.building
                    ),
                    temperature_air_heating_in_celsius=15,
                    temperature_air_cooling_in_celsius=25,
                    offset_in_celsius=0,
                    mode=1,
                )
                self.wrapped_controllers.append(
                    GenericHeatPumpController(
                        my_simulation_parameters=self.my_simulation_parameters,
                        config=heat_pump_config,
                    )
                )

            elif "EVCharger" in controller_name:
                self.wrapped_controllers.append(
                    EVChargerController(
                        my_simulation_parameters=self.my_simulation_parameters,
                        config=EVChargerControllerConfig.get_default_config(),
                    )
                )

        self.add_wrapped_controller_inputs_and_outputs()

    def connect_similar_inputs(self, components: Union[List[Component], Component]) -> None:
        """Connect matching inputs of wrapped controllers to a component's outputs.

        Iterates over each wrapped controller's input channels and connects them
        to outputs of ``components`` that share the same ``field_name``.

        Args:
            components: A single Component or list of components whose outputs
                should be connected to matching wrapped-controller inputs.

        Raises:
            ValueError: If the smart controller has no inputs, or if no matching
                inputs are found for a given component.
            TypeError: If an element of ``components`` is not a Component.
        """
        if len(self.inputs) == 0:
            raise ValueError(f"The component {self.component_name} has no inputs.")

        if not isinstance(components, list):
            components = [components]

        for component in components:
            if isinstance(component, Component) is False:
                raise TypeError("Input variable is not a component")
            connection_found = False
            index: Optional[int] = None
            for index, _ in enumerate(self.wrapped_controllers):
                for input_channel in self.wrapped_controllers[index].inputs:
                    for output in component.outputs:
                        if input_channel.field_name == output.field_name:
                            connection_found = True
                            self.wrapped_controllers[index].connect_input(
                                self.wrapped_controllers[index].field_name,
                                component.component_name,
                                output.field_name,
                            )
            if not connection_found and index is not None:
                raise ValueError(
                    f"No similar inputs from {self.wrapped_controllers[index].component_name} are compatible with the outputs of {component.component_name}!"
                )

    def add_wrapped_controller_inputs_and_outputs(self) -> None:
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
        """Perform optional post-simulation consistency checks.

        Currently a no-op; included to satisfy the Component interface.

        Args:
            timestep: Current simulation timestep index.
            stsv: Single-time-step values container.
        """
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate all wrapped controllers for the given timestep.

        Delegates to each wrapped controller's ``i_simulate`` method.

        Args:
            timestep: Current simulation timestep index.
            stsv: Single-time-step values container for reading inputs and
                writing outputs.
            force_convergence: If True, wrapped controllers should attempt to
                force convergence.
        """
        for index, _ in enumerate(self.wrapped_controllers):
            self.wrapped_controllers[index].i_simulate(
                timestep=timestep, stsv=stsv, force_convergence=force_convergence
            )

    def connect_electricity(self, component: Component) -> None:
        """Connect the electricity input of each wrapped controller to a component.

        For every wrapped controller that exposes an ``ElectricityInput`` attribute,
        connects it to the ``ElectricityOutput`` of the provided component.

        Args:
            component: The component providing the electricity output.

        Raises:
            TypeError: If ``component`` is not a Component.
            AttributeError: If ``component`` has no ``ElectricityOutput`` attribute.
        """
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
