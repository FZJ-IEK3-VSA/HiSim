
""" Contains all the main simulator components. """

import os
import datetime
from typing import List, Tuple, Dict, Any, Optional
import time

import pandas as pd

# Owned
from hisim import sim_repository
from hisim.postprocessing import postprocessing_main as pp
import hisim.component as cp
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import utils


__authors__ = "Noah Pflugradt, Vitor Hugo Bellotto Zago, Maximillian Hillen"
__copyright__ = "Copyright 2020-2022, FZJ-IEK-3"
__license__ = "MIT"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "production"


class ComponentWrapper:

    """ Wraps components for use. """

    def __init__(self, component: cp.Component, is_cachable: bool):
        """ Initializes the component wrapper.

        Used to handle the connection of inputs and outputs.
        """
        self.my_component = component
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
                    raise Exception("trying to register the same key twice: " + col.full_name)
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


class Simulator:

    """ Core class of HiSim: Runs the main loop. """

    @utils.measure_execution_time
    def __init__(self, module_directory: str, setup_function: str, my_simulation_parameters: Optional[SimulationParameters]) -> None:
        """ Initializes the simulator class and creates the result directory. """
        if setup_function is None:
            raise Exception("No setup function was set")
        self.setup_function = setup_function
        self._simulation_parameters: SimulationParameters
        if my_simulation_parameters is not None:
            self._simulation_parameters = my_simulation_parameters
            log.LOGGING_LEVEL = self._simulation_parameters.logging_level
        self.wrapped_components: List[ComponentWrapper] = []
        self.all_outputs: List[cp.ComponentOutput] = []
        self.module_directory = module_directory
        self.simulation_repository = sim_repository.SimRepository()

    def set_simulation_parameters(self,  my_simulation_parameters: SimulationParameters) -> None:
        """ Sets the simulation parameters and the logging level at the same time. """
        self._simulation_parameters = my_simulation_parameters
        if self._simulation_parameters is not None:
            log.LOGGING_LEVEL = self._simulation_parameters.logging_level

    def add_component(self, component: cp.Component, is_cachable: bool = False) -> None:
        """ Adds component to simulator and wraps it up the output in the register. """
        if self._simulation_parameters is None:
            raise Exception("Simulation Parameters were not initialized")
        # set the repository
        component.set_sim_repo(self.simulation_repository)

        # set the wrapper
        wrap = ComponentWrapper(component, is_cachable)
        wrap.register_component_outputs(self.all_outputs)
        self.wrapped_components.append(wrap)

    @utils.measure_execution_time
    def connect_all_components(self) -> None:
        """ Connects the inputs from every component to the corresponding outputs. """
        for wrapped_component in self.wrapped_components:
            wrapped_component.connect_inputs(self.all_outputs)

    def process_one_timestep(self, timestep: int) -> Tuple[cp.SingleTimeStepValues, int]:
        """ Executes one simulation timestep.

        Some components can be connected in a circle.
        To solve the circular dependency, all components have their states restored
        and simulated until their values converge.

        Firstly, their previously converged state is saved as the current timestep state.
        Following up, all components have their states restored and simulated respectively.
        Convergence is dependent on the i_restore and i_simulate of the components and how they
        are connected to each other.
        """

        # Save states of all components
        # Executes save state in the component
        for wrapped_component in self.wrapped_components:
            wrapped_component.save_state()

        continue_calculation = True

        # Verifies data existence
        if (len(self.all_outputs)) == 0:
            raise Exception("Not a single column was defined.")

        # Saves number of outputs
        number_of_outputs = len(self.all_outputs)

        # Creates List with values
        stsv = cp.SingleTimeStepValues(number_of_outputs)
        # Creates a buffer List with values
        previous_values = cp.SingleTimeStepValues(number_of_outputs)
        iterative_tries = 0
        force_convergence = False

        # Starts loop
        while continue_calculation:
            # Loops through components
            for wrapped_component in self.wrapped_components:
                # Executes restore state for each component
                wrapped_component.restore_state()
                # Executes i_simulate for component
                wrapped_component.calculate_component(timestep, stsv, force_convergence)

            # Stops simulation for too small difference between
            # actual values and previous values
            if stsv.is_close_enough_to_previous(previous_values):
                continue_calculation = False

            if iterative_tries > 10:
                force_convergence = True
            if iterative_tries > 100:
                list_of_changed_values = stsv.get_differences_for_error_msg(previous_values, self.all_outputs)
                raise Exception("More than 100 tries in time step " + str(timestep) + "\n" + list_of_changed_values)
            # Copies actual values to previous variable
            previous_values.copy_values_from_other(stsv)
            iterative_tries += 1

        for wrapped_component in self.wrapped_components:
            wrapped_component.doublecheck(timestep, stsv)

        return (stsv, iterative_tries)

    def prepare_simulation_directory(self):
        """ Prepares the simulation directory. Determines the filename if nothing is set. """
        if self._simulation_parameters.result_directory is None or len(self._simulation_parameters.result_directory) == 0:
            result_dirname = f"{self.setup_function.lower()}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self._simulation_parameters.result_directory = os.path.join(self.module_directory, "results", result_dirname)

        if not os.path.isdir(self._simulation_parameters.result_directory):
            os.makedirs(self._simulation_parameters.result_directory, exist_ok=True)
        log.information("Using result directory: " + self._simulation_parameters.result_directory)
        log.LOGGING_LEVEL = self._simulation_parameters.logging_level

    # @profile
    # @utils.measure_execution_time
    def run_all_timesteps(self) -> None:
        """ Performs all the timesteps of the simulation and saves the results in the attribute results. """
        # Error Tests
        # Test if all parameters were initialized
        if self._simulation_parameters is None:
            raise Exception("Simulation Parameters were not initialized")

        # Tests if wrapper has any components at all
        if len(self.wrapped_components) == 0:
            raise Exception("Not a single component was defined. Quitting.")

        flagfile = os.path.join(self._simulation_parameters.result_directory, "finished.flag")
        if self._simulation_parameters.skip_finished_results and os.path.exists(flagfile):
            log.warning("Found " + flagfile + ". This calculation seems finished. Quitting.")
            return

        self.prepare_simulation_directory()

        # Starts time counter
        start_counter = time.perf_counter()

        # Connects all components
        self.connect_all_components()
        log.information("finished connecting all components. A total of " + str(
            len(self.wrapped_components)) + " components were defined. They have a total of " + str(len(self.all_outputs)) + " outputs.")
        all_result_lines = []
        log.information("Starting simulation for " + str(self._simulation_parameters.timesteps) + " timesteps")
        lastmessage = datetime.datetime.now()
        starttime = datetime.datetime.now()
        total_iteration_tries = 0

        for step in range(self._simulation_parameters.timesteps):
            if self._simulation_parameters.timesteps % 500 == 0:
                log.information("Starting step " + str(step))

            (resulting_stsv, iteration_tries) = self.process_one_timestep(step)

            # Accumulates iteration counter
            total_iteration_tries += iteration_tries

            # Appends
            all_result_lines.append(resulting_stsv.values)
            del resulting_stsv
            # Calculates time execution
            elapsed = datetime.datetime.now() - lastmessage

            # For simulation longer than 5 seconds
            if elapsed.total_seconds() > 5:
                lastmessage = self.show_progress(starttime, step, total_iteration_tries)

        postprocessing_datatransfer = self.prepare_post_processing(all_result_lines, start_counter)
        log.information("Starting postprocessing")
        if postprocessing_datatransfer is None:
            raise Exception("postprocessing_datatransfer was none")

        my_post_processor = pp.PostProcessor()
        my_post_processor.run(ppdt=postprocessing_datatransfer)
        for wrapped_component in self.wrapped_components:
            wrapped_component.clear()
        del all_result_lines
        del postprocessing_datatransfer
        del my_post_processor
        self.simulation_repository.clear()
        log.information("Finished postprocessing")
        with open(flagfile, 'a', encoding="utf-8") as filestream:
            filestream.write("finished")

    @utils.measure_execution_time
    def prepare_post_processing(self, all_result_lines, start_counter):
        """ Prepares the post processing. """
        log.information("Preparing post processing")
        """  Prepares the results from the simulation for the post processing. """
        if len(all_result_lines) != self._simulation_parameters.timesteps:
            raise Exception("not all lines were generated")
        colum_names = []
        if self.setup_function is None:
            raise Exception("No setup function was set")
        entry: cp.ComponentOutput
        for index, entry in enumerate(self.all_outputs):
            column_name = entry.get_pretty_name()
            colum_names.append(column_name)
            log.debug("Output column: " + column_name)
        results_data_frame = pd.DataFrame(data=all_result_lines, columns=colum_names)
        # todo: fix this constant
        index = pd.date_range("2021-01-01 00:00:00", periods=len(results_data_frame), freq="T")
        results_data_frame.index = index
        end_counter = time.perf_counter()
        execution_time = end_counter - start_counter
        log.information(f"Simulation took {execution_time:4.0f}s")
        results_merged = self.get_std_results(results_data_frame)

        ppdt = pp.PostProcessingDataTransfer(
            results=results_data_frame,
            all_outputs=self.all_outputs,
            simulation_parameters=self._simulation_parameters,
            wrapped_components=self.wrapped_components,
            mode=1,
            setup_function=self.setup_function,
            execution_time=execution_time,
            results_monthly=results_merged,
        )
        log.information("Finished preparing post processing")
        return ppdt

    def show_progress(self, starttime: datetime.datetime, step: int, total_iteration_tries: int) -> datetime.datetime:
        """ Makes the pretty progress messages with time estimate. """
        # calculates elapsed time
        elapsed = datetime.datetime.now() - starttime
        elapsed_minutes, elapsed_seconds = divmod(elapsed.seconds, 60)
        elapsed_seconds_str: str = str(elapsed_seconds).zfill(2)
        # Calculates steps achieved per time duration
        steps_per_second = step / elapsed.total_seconds()
        if step == 0:
            average_iteration_tries: float = 1
        else:
            average_iteration_tries = total_iteration_tries / step
        time_elapsed = datetime.timedelta(seconds=((self._simulation_parameters.timesteps - step) / steps_per_second))
        time_left_minutes, time_left_seconds = divmod(time_elapsed.seconds, 60)
        time_left_seconds = str(time_left_seconds).zfill(2)  # type: ignore
        simulation_status = f"Simulating... {(step / self._simulation_parameters.timesteps) * 100:.1f}% "
        simulation_status += f"| Elapsed Time: {elapsed_minutes}:{elapsed_seconds_str} min "
        simulation_status += f"| Speed: {steps_per_second:.0f} step/s "
        simulation_status += f"| Time Left: {time_left_minutes}:{time_left_seconds} min"
        simulation_status += f"| Avg. iterations {average_iteration_tries:.1f}"
        log.information(simulation_status)
        return datetime.datetime.now()

    def get_std_results(self, results_data_frame: pd.DataFrame) -> pd.DataFrame:
        """ Converts results into a pretty dataframe for post processing. """
        pd_timeline = pd.date_range(start=self._simulation_parameters.start_date,
                                    end=self._simulation_parameters.end_date,
                                    freq=f'{self._simulation_parameters.seconds_per_timestep}S')[:-1]
        n_columns = results_data_frame.shape[1]
        results_data_frame.index = pd_timeline
        results_merged = pd.DataFrame()
        for i_column in range(n_columns):
            temp_df = pd.DataFrame(results_data_frame.values[:, i_column], index=pd_timeline,
                                   columns=[results_data_frame.columns[i_column]])
            if 'Temperature' in results_data_frame.columns[i_column] or 'Percent' in results_data_frame.columns[i_column]:
                temp_df = temp_df.resample('M').interpolate(method='linear')
            else:
                temp_df = temp_df.resample('M').sum()
            results_merged[temp_df.columns[0]] = temp_df.values[:, 0]
            results_merged.index = temp_df.index
        return results_merged
