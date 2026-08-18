"""Wraps components for use in the simulator."""

# clean
from typing import List

import hisim.component as cp
import hisim.loadtypes as lt
from hisim import log


class ComponentWrapper:
    """Wraps a Component for use in the simulator.

    Manages the component's inputs and outputs, registers outputs in the
    global list, connects inputs to matching outputs, and delegates state
    save/restore and simulation calls to the wrapped component.
    """

    def __init__(self, component: cp.Component, is_cachable: bool, connect_automatically: bool):
        """Initialize the wrapper with a component and caching/connect flags.

        Args:
            component: The Component instance to wrap.
            is_cachable: Whether the component's simulation results may be cached.
            connect_automatically: Whether inputs should be connected automatically
                during wiring.
        """
        self.my_component: cp.Component = component
        self.component_inputs: List[cp.ComponentInput] = []
        self.component_outputs: List[cp.ComponentOutput] = []
        # self.cachedict: = {}
        self.is_cachable: bool = is_cachable
        self.connect_automatically: bool = connect_automatically

    def clear(self) -> None:
        """Clears properties to help with saving memory."""
        del self.my_component
        del self.component_inputs
        del self.component_outputs

    def register_component_outputs(
        self, all_outputs: List[cp.ComponentOutput], wrapped_components_so_far: List["ComponentWrapper"]
    ) -> None:
        """Register the wrapped component's outputs in the global outputs list.

        Filters out dynamic outputs whose source component has not yet been
        wrapped, assigns each remaining output a global index, and appends it
        to both the global list and this wrapper's component_outputs.

        Args:
            all_outputs: Global list of all ComponentOutput objects registered so far.
            wrapped_components_so_far: ComponentWrappers already registered, used to
                check whether dynamic-output source components exist.

        Raises:
            ValueError: If an output with the same full_name is already registered,
                or if the component ends up with no outputs registered.
        """

        log.debug(f"Registering component outputs on {self.my_component.component_name}")

        # Collect classnames of already wrapped components once
        wrapped_class_names_so_far = {component.my_component.get_classname() for component in wrapped_components_so_far}

        # Filter dynamic outputs if present and remove those which do not have a corresponding source component
        if hasattr(self.my_component, "my_component_outputs"):
            dynamic_outputs = self.my_component.my_component_outputs
            filtered_outputs = []
            for dynamic_output in dynamic_outputs:
                if (
                    dynamic_output.source_component_class
                    and dynamic_output.source_component_class not in wrapped_class_names_so_far
                ):
                    log.debug(
                        f"Dynamic output {dynamic_output.source_output_field_name} cannot be registered because its source component "
                        f"{dynamic_output.source_component_class} is not wrapped yet. "
                        f"Wrapped components so far: {wrapped_class_names_so_far}. "
                        "Therefore, this dynamic output will be skipped."
                    )
                    continue
                filtered_outputs.append(dynamic_output)
            # Return the filtered dynamic outputs to the component
            self.my_component.my_component_outputs = filtered_outputs

        # register and process the output column
        outputs = self.my_component.get_outputs()
        for output in outputs:
            if (
                output.source_component_class is not None
                and output.source_component_class not in wrapped_class_names_so_far
            ):
                log.debug(
                    f"Component output {output.full_name} cannot be registered because its source component "
                    f"{output.source_component_class} is not wrapped yet. "
                    f"Wrapped components so far: {wrapped_class_names_so_far}. "
                    "Therefore, this output will be skipped."
                )
                continue  # skip this output, because the source component is not wrapped yet
            if any(output.full_name == out.full_name for out in all_outputs):
                raise ValueError(
                    f"Trying to register the same key twice: {output.full_name}. "
                    "Check if more than one building is being modeled."
                )
            # set the global index of the output column
            output.global_index = len(all_outputs)  # noqa
            # add the output column to the global list of outputs
            all_outputs.append(output)
            self.component_outputs.append(output)
            log.debug(f"Registered output {output.full_name}")
            if not self.component_outputs:
                raise ValueError(f"The component {self.my_component.component_name} has no outputs registered.")

    def register_component_inputs(self, global_column_dict: dict[str, cp.ComponentInput]) -> None:
        """Register the wrapped component's inputs from the global column dict.

        Args:
            global_column_dict: Mapping from input full name to ComponentInput
                containing all inputs registered across all components.
        """

        log.debug(f"Registering component inputs for {self.my_component.component_name}")
        # look up input columns and cache, so we only have the correct columns saved
        input_columns: List[cp.ComponentInput] = self.my_component.get_input_definitions()
        for col in input_columns:
            global_column_entry = global_column_dict[col.fullname]
            self.component_inputs.append(global_column_entry)

    def save_state(self) -> None:
        """Saves the state.

        This gets called at the beginning of a timestep and wraps the i_save_state
        i_save_state should always cache the current state at the beginning of a time step.
        """
        self.my_component.i_save_state()

    def doublecheck(self, timestep: int, single_time_step_values: cp.SingleTimeStepValues) -> None:
        """Delegate to the component's i_doublecheck for optional post-iteration checks.

        Args:
            timestep: The current simulation timestep index.
            single_time_step_values: The converged values for this timestep.
        """
        self.my_component.i_doublecheck(timestep, single_time_step_values)

    def restore_state(self) -> None:
        """Wrapper for i_restore_state.

        Gets called at the beginning of every iteration to return to the state at the beginning of the iteration.
        """
        self.my_component.i_restore_state()

    def calculate_component(self, timestep: int, single_time_step_values: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Delegate to the component's i_simulate for one simulation step.

        Args:
            timestep: The current simulation timestep index.
            single_time_step_values: The values vector for this timestep, read from
                and written to by the component.
            force_convergence: Whether to force convergence regardless of the
                component's internal convergence check.
        """
        self.my_component.i_simulate(timestep, single_time_step_values, force_convergence)

    def prepare_calculation(self) -> None:
        """Wrapper for i_prepare_calculation."""
        log.information(f"Preparing {self.my_component.component_name} for simulation.")
        self.my_component.i_prepare_simulation()

    def connect_inputs(self, all_outputs: List[cp.ComponentOutput]) -> None:
        """Connect each of the component's inputs to a matching global output.

        Matches by source component name and field name, verifying load-type and unit
        compatibility. Two concretely differing load types or units are a hard error.
        ``LoadTypes.ANY`` on either side is a legitimate wildcard (control and state
        signals) and connects silently; ``Units.ANY`` mismatches log a warning.

        Args:
            all_outputs: Global list of all ComponentOutput objects to match against.

        Raises:
            ValueError: If a matched input and output have incompatible load types or
                units, or if a mandatory input has no matching output.
        """

        # Returns a List of ComponentInputs
        self.my_component.get_input_definitions()

        # Loop through lists of inputs of self component
        for component_input in self.my_component.inputs:
            # Adds to the ComponentInput List of ComponentWrapper
            self.component_inputs.append(component_input)

            # Creates a ComponentOutput variable
            global_output: cp.ComponentOutput

            # Loop through all the existent component outputs in the current simulation
            for global_output in all_outputs:
                # Check if ComponentOutput and ComponentInput match
                if (
                    global_output.component_name == component_input.src_object_name
                    and global_output.field_name == component_input.src_field_name
                ):
                    # Check if ComponentOutput and ComponentInput have the same load type.
                    # LoadTypes.ANY on either side is a legitimate wildcard (control and
                    # state signals) and connects without any diagnostics.
                    if component_input.loadtype != global_output.load_type:
                        if lt.LoadTypes.ANY not in (component_input.loadtype, global_output.load_type):
                            raise ValueError(
                                f"The input {component_input.field_name} (cp: {component_input.component_name}, "
                                f"load type: {component_input.loadtype}) and output {global_output.field_name} "
                                f"(cp: {global_output.component_name}, load type: {global_output.load_type}) "
                                f"do not have the same load type! Align the two port declarations (see the "
                                f"LoadTypes canonicalization convention: temperature signals are TEMPERATURE, "
                                f"water mass flows are WARM_WATER)."
                            )
                    # Check if ComponentOutput and ComponentInput have the same units
                    if component_input.unit != global_output.unit:
                        # Check the use of "Units.Any"
                        if (component_input.unit == lt.Units.ANY and global_output.unit != lt.Units.ANY) or (
                            component_input.unit != lt.Units.ANY and global_output.unit == lt.Units.ANY
                        ):
                            log.warning(
                                f"The input {component_input.field_name} (cp: {component_input.component_name}, unit: {component_input.unit}) "
                                f"and output {global_output.field_name}(cp: {global_output.component_name}, unit: {global_output.unit}) "
                                f"might not have compatible units."
                            )  #
                            # Connect, i.e, save ComponentOutput in ComponentInput
                            component_input.source_output = global_output
                            log.debug(f"Connected input '{component_input.fullname}' to '{global_output.full_name}'")
                        else:
                            raise ValueError(
                                f"The input {component_input.field_name} (cp: {component_input.component_name}, unit: {component_input.unit}) and "
                                f"output {global_output.field_name}(cp: {global_output.component_name}, unit: {global_output.unit}) "
                                f"do not have the same unit!"
                            )  #
                    else:
                        # Connect, i.e, save ComponentOutput in ComponentInput
                        component_input.source_output = global_output
                        log.debug(f"connected input {component_input.fullname} to {global_output.full_name}")

            # Check if there are inputs that have been not connected
            if component_input.is_mandatory and component_input.source_output is None:
                if component_input.allow_unconnected_mandatory:
                    log.warning(
                        f"The input {component_input.field_name} (cp: {component_input.component_name}, "
                        f"unit: {component_input.unit}) is not connected to any ComponentOutput. "
                        "This mandatory input is marked as allow_unconnected_mandatory, so the simulation proceeds without it. "
                        "Likely, the source component does not provide this output in the current configuration."
                    )
                else:
                    raise ValueError(
                        f"The ComponentInput {component_input.field_name} (cp: {component_input.component_name}, "
                        f"unit: {component_input.unit}) is not connected to any ComponentOutput. "
                        "You could run debug mode (logging_level=4) to check all inputs, outputs and connections. "
                        f"Likely, no match was found between {component_input.src_object_name} and {[output.component_name for output in all_outputs]} & "
                        f"and between {component_input.src_field_name} and {[output.field_name for output in all_outputs]}."
                    )  #
