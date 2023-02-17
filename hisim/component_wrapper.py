""" Wraps components for use in the simulator. """
# clean
from typing import List, Dict, Any

import hisim.component as cp
import hisim.loadtypes as lt
from hisim import log


class ComponentWrapper:

    """ Wraps components for use. """

    def __init__(self, component: cp.Component, is_cachable: bool):
        """ Initializes the component wrapper.

        Used to handle the connection of inputs and outputs.
        """
        self.my_component: cp.Component = component
        self.component_inputs: List[cp.ComponentInput] = []
        self.component_outputs: List[cp.ComponentOutput] = []
        # self.cachedict: = {}
        self.is_cachable = is_cachable

    def clear(self):
        """ Clears properties to help with saving memory. """
        del self.my_component
        del self.component_inputs
        del self.component_outputs

    def register_component_outputs(self, all_outputs: List[cp.ComponentOutput]) -> None:
        """ Registers component outputs in the global list of components. """
        log.information("Registering component outputs on " + self.my_component.component_name)
        # register the output column
        output_columns = self.my_component.get_outputs()
        for col in output_columns:
            col.global_index = len(all_outputs)  # noqa
            for output in all_outputs:
                if output.full_name == col.full_name:
                    raise ValueError("trying to register the same key twice: " + col.full_name)
            all_outputs.append(col)
            log.debug("Registered output " + col.full_name)
            self.component_outputs.append(col)

    def register_component_inputs(self, global_column_dict: Dict[str, Any]) -> None:
        """ Gets the inputs for the current component from the global column dict and puts them into component_inputs. """
        log.information("Registering component inputs for " + self.my_component.component_name)
        # look up input columns and cache, so we only have the correct columns saved
        input_columns: List[cp.ComponentInput] = self.my_component.get_input_definitions()
        for col in input_columns:
            global_column_entry = global_column_dict[col.fullname]
            self.component_inputs.append(global_column_entry)

    def save_state(self) -> None:
        """ Saves the state.

        This gets called at the beginning of a timestep and wraps the i_save_state
        i_save_state should always cache the current state at the beginning of a time step.
        """
        self.my_component.i_save_state()

    def doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """  Wrapper for i_doublecheck.

        Doublecheck is completely optional call that can be used while debugging to
        double check the component results after the iteration finished for a timestep.
        """
        self.my_component.i_doublecheck(timestep, stsv)

    def restore_state(self) -> None:
        """ Wrapper for i_restore_state.

        Gets called at the beginning of every iteration to return to the state at the beginning of the iteration.
        """
        self.my_component.i_restore_state()

    def calculate_component(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """ Wrapper for the core simulation function in each component. """
        self.my_component.i_simulate(timestep, stsv, force_convergence)

    def prepare_calculation(self):
        """ Wrapper for i_prepare_calculation. """
        log.information("Preparing " + self.my_component.component_name + " for simulation.")
        self.my_component.i_prepare_simulation()

    def connect_inputs(self, all_outputs: List[cp.ComponentOutput]) -> None:
        """ Connects cp.ComponentOutputs to ComponentInputs of WrapperComponent. """
        # Returns a List of ComponentInputs
        self.my_component.get_input_definitions()

        # Loop through lists of inputs of self component
        for cinput in self.my_component.inputs:
            # Adds to the ComponentInput List of ComponentWrapper
            self.component_inputs.append(cinput)

            # Creates a ComponentOutput variable
            global_output: cp.ComponentOutput

            # Loop through all the existent component outputs in the current simulation
            for global_output in all_outputs:
                # Check if ComponentOutput and ComponentInput match
                if global_output.component_name == cinput.src_object_name and global_output.field_name == cinput.src_field_name:
                    # Check if ComponentOutput and ComponentInput have the same units
                    if cinput.unit != global_output.unit:
                        # Check the use of "Units.Any"
                        if (cinput.unit == lt.Units.ANY and global_output.unit != lt.Units.ANY) or (
                                cinput.unit != lt.Units.ANY and global_output.unit == lt.Units.ANY):
                            log.warning(
                                f"The input {cinput.field_name} (cp: {cinput.component_name}, unit: {cinput.unit}) "
                                f"and output {global_output.field_name}(cp: {global_output.component_name}, unit: {global_output.unit}) "
                                f"might not have compatible units.")  #
                            # Connect, i.e, save ComponentOutput in ComponentInput
                            cinput.source_output = global_output
                            log.debug("Connected input '" + cinput.fullname + "' to '" + global_output.full_name + "'")
                        else:
                            raise SystemError(
                                f"The input {cinput.field_name} (cp: {cinput.component_name}, unit: {cinput.unit}) and "
                                f"output {global_output.field_name}(cp: {global_output.component_name}, unit: {global_output.unit}) "
                                f"do not have the same unit!")  #
                    else:
                        # Connect, i.e, save ComponentOutput in ComponentInput
                        cinput.source_output = global_output
                        log.debug(f"connected input {cinput.fullname} to {global_output.full_name}")

            # Check if there are inputs that have been not connected
            if cinput.is_mandatory and cinput.source_output is None:
                raise SystemError(
                    f"The ComponentInput {cinput.field_name} (cp: {cinput.component_name}, "
                    f"unit: {cinput.unit}) is not connected to any ComponentOutput.")  #
