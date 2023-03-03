"""The Simulator class forms the framework for all HiSim simulations.

It iterates over all components in each timestep until convergence and loops over all time steps.
"""
# clean
import os
import datetime
from typing import List, Tuple, Optional, cast
import time

import pandas as pd

# Owned
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.component_wrapper import ComponentWrapper
from hisim import sim_repository
from hisim.postprocessing import postprocessing_main as pp
import hisim.component as cp
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim import utils


__authors__ = "Noah Pflugradt, Vitor Hugo Bellotto Zago, Maximillian Hillen"
__copyright__ = "Copyright 2020-2022, FZJ-IEK-3"
__license__ = "MIT"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "production"


class Simulator:

    """Core class of HiSim: Runs the main loop."""

    @utils.measure_execution_time
    def __init__(
        self,
        module_directory: str,
        setup_function: str,
        my_simulation_parameters: Optional[SimulationParameters],
    ) -> None:
        """Initializes the simulator class and creates the result directory."""
        if setup_function is None:
            raise ValueError("No setup function was set")
        self.setup_function = setup_function
        self._simulation_parameters: SimulationParameters
        if my_simulation_parameters is not None:
            self._simulation_parameters = my_simulation_parameters
            log.LOGGING_LEVEL = self._simulation_parameters.logging_level
        self.wrapped_components: List[ComponentWrapper] = []
        self.all_outputs: List[cp.ComponentOutput] = []
        self.module_directory = module_directory
        self.simulation_repository = sim_repository.SimRepository()
        self.results_data_frame: pd.DataFrame

    def set_simulation_parameters(
        self, my_simulation_parameters: SimulationParameters
    ) -> None:
        """Sets the simulation parameters and the logging level at the same time."""
        self._simulation_parameters = my_simulation_parameters
        if self._simulation_parameters is not None:
            log.LOGGING_LEVEL = self._simulation_parameters.logging_level

    def add_component(self, component: cp.Component, is_cachable: bool = False) -> None:
        """Adds component to simulator and wraps it up the output in the register."""
        if self._simulation_parameters is None:
            raise ValueError("Simulation Parameters were not initialized")
        # set the repository
        component.set_sim_repo(self.simulation_repository)

        # set the wrapper
        wrap = ComponentWrapper(component, is_cachable)
        wrap.register_component_outputs(self.all_outputs)
        self.wrapped_components.append(wrap)

    @utils.measure_execution_time
    def connect_all_components(self) -> None:
        """Connects the inputs from every component to the corresponding outputs."""
        for wrapped_component in self.wrapped_components:
            wrapped_component.connect_inputs(self.all_outputs)

    @utils.measure_execution_time
    def prepare_calculation(self) -> None:
        """Connects the inputs from every component to the corresponding outputs."""
        for wrapped_component in self.wrapped_components:
            wrapped_component.prepare_calculation()

    def process_one_timestep(
        self, timestep: int, previous_stsv: cp.SingleTimeStepValues
    ) -> Tuple[cp.SingleTimeStepValues, int]:
        """Executes one simulation timestep.

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
            raise ValueError("Not a single column was defined.")

        # Creates List with values
        stsv = previous_stsv.clone()
        # Creates a buffer List with values
        previous_values = previous_stsv.clone()
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
                list_of_changed_values = stsv.get_differences_for_error_msg(
                    previous_values, self.all_outputs
                )
                raise ValueError(
                    "More than 100 tries in time step "
                    + str(timestep)
                    + "\n"
                    + list_of_changed_values
                )
            # Copies actual values to previous variable
            previous_values.copy_values_from_other(stsv)
            iterative_tries += 1

        for wrapped_component in self.wrapped_components:
            wrapped_component.doublecheck(timestep, stsv)
        return (stsv, iterative_tries)

    def prepare_simulation_directory(self):
        """Prepares the simulation directory. Determines the filename if nothing is set."""
        if (
            self._simulation_parameters.result_directory is None
            or len(self._simulation_parameters.result_directory) == 0
        ):
            result_dirname = f"{self.setup_function.lower()}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self._simulation_parameters.result_directory = os.path.join(
                self.module_directory, "results", result_dirname
            )

        if not os.path.isdir(self._simulation_parameters.result_directory):
            os.makedirs(self._simulation_parameters.result_directory, exist_ok=True)
        log.information(
            "Using result directory: " + self._simulation_parameters.result_directory
        )
        log.LOGGING_LEVEL = self._simulation_parameters.logging_level

    # @profile
    # @utils.measure_execution_time
    def run_all_timesteps(self) -> None:
        """Performs all the timesteps of the simulation and saves the results in the attribute results."""
        # Error Tests
        # Test if all parameters were initialized
        if self._simulation_parameters is None:
            raise ValueError("Simulation Parameters were not initialized")

        # Tests if wrapper has any components at all
        if len(self.wrapped_components) == 0:
            raise ValueError("Not a single component was defined. Quitting.")
        # call again because it might not have gotten executed depending on how it's called.
        self.prepare_simulation_directory()
        flagfile = os.path.join(
            self._simulation_parameters.result_directory, "finished.flag"
        )
        if self._simulation_parameters.skip_finished_results and os.path.exists(
            flagfile
        ):
            log.warning(
                "Found " + flagfile + ". This calculation seems finished. Quitting."
            )
            return
        # Starts time counter
        start_counter = time.perf_counter()
        self.prepare_calculation()
        # Connects all components
        self.connect_all_components()
        log.information(
            "finished connecting all components. A total of "
            + str(len(self.wrapped_components))
            + " components were defined. They have a total of "
            + str(len(self.all_outputs))
            + " outputs."
        )
        all_result_lines = []
        log.information(
            "Starting simulation for "
            + str(self._simulation_parameters.timesteps)
            + " timesteps"
        )
        lastmessage = datetime.datetime.now()
        last_step: int = 0
        starttime = datetime.datetime.now()
        total_iteration_tries_since_last_msg = 0

        # Creates empty list with values to get started
        number_of_outputs = len(self.all_outputs)
        stsv = cp.SingleTimeStepValues(number_of_outputs)

        for step in range(self._simulation_parameters.timesteps):
            if self._simulation_parameters.timesteps % 500 == 0:
                log.information("Starting step " + str(step))

            (resulting_stsv, iteration_tries) = self.process_one_timestep(step, stsv)
            stsv = cp.SingleTimeStepValues(number_of_outputs)
            # Accumulates iteration counter
            total_iteration_tries_since_last_msg += iteration_tries

            # Appends
            all_result_lines.append(resulting_stsv.values)
            del resulting_stsv
            # Calculates time execution
            elapsed = datetime.datetime.now() - lastmessage

            # For simulation longer than 5 seconds
            if elapsed.total_seconds() > 5:
                lastmessage = self.show_progress(
                    starttime, step, total_iteration_tries_since_last_msg, last_step
                )
                last_step = step
                total_iteration_tries_since_last_msg = 0
        postprocessing_datatransfer = self.prepare_post_processing(
            all_result_lines, start_counter
        )
        log.information("Starting postprocessing")
        if postprocessing_datatransfer is None:
            raise ValueError("postprocessing_datatransfer was none")

        my_post_processor = pp.PostProcessor()
        my_post_processor.run(ppdt=postprocessing_datatransfer)
        for wrapped_component in self.wrapped_components:
            wrapped_component.clear()
        del all_result_lines
        del postprocessing_datatransfer
        del my_post_processor
        self.simulation_repository.clear()
        log.information("Finished postprocessing")
        with open(flagfile, "a", encoding="utf-8") as filestream:
            filestream.write("finished")

    @utils.measure_execution_time
    def prepare_post_processing(self, all_result_lines, start_counter):
        """Prepares the post processing."""
        log.information("Preparing post processing")
        # Prepares the results from the simulation for the post processing.
        if len(all_result_lines) != self._simulation_parameters.timesteps:
            raise ValueError("not all lines were generated")
        colum_names = []
        if self.setup_function is None:
            raise ValueError("No setup function was set")
        entry: cp.ComponentOutput
        for _index, entry in enumerate(self.all_outputs):
            column_name = entry.get_pretty_name()
            colum_names.append(column_name)
            log.debug("Output column: " + column_name)
        self.results_data_frame = pd.DataFrame(
            data=all_result_lines, columns=colum_names
        )
        # todo: fix this constant
        df_index = pd.date_range(
            "2021-01-01 00:00:00", periods=len(self.results_data_frame), freq="T"
        )
        self.results_data_frame.index = df_index
        end_counter = time.perf_counter()
        execution_time = end_counter - start_counter
        log.information(f"Simulation took {execution_time:1.2f}s.")
        results_merged = self.get_std_results(self.results_data_frame)
        ppdt = PostProcessingDataTransfer(
            results=self.results_data_frame,
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

    def show_progress(
        self,
        starttime: datetime.datetime,
        step: int,
        total_iteration_tries: int,
        last_step: int,
    ) -> datetime.datetime:
        """Makes the pretty progress messages with time estimate."""
        # calculates elapsed time
        elapsed = datetime.datetime.now() - starttime
        elapsed_minutes, elapsed_seconds = divmod(elapsed.seconds, 60)
        elapsed_seconds_str: str = str(elapsed_seconds).zfill(2)
        # Calculates steps achieved per time duration
        steps_per_second = step / elapsed.total_seconds()
        elapsed_steps: int = step - last_step
        if elapsed_steps == 0:
            average_iteration_tries: float = 1
        else:
            average_iteration_tries = total_iteration_tries / elapsed_steps
        time_elapsed = datetime.timedelta(
            seconds=(self._simulation_parameters.timesteps - step) / steps_per_second
        )
        time_left_minutes, time_left_seconds = divmod(time_elapsed.seconds, 60)
        time_left_seconds = str(time_left_seconds).zfill(2)  # type: ignore
        simulation_status = f"Simulating... {(step / self._simulation_parameters.timesteps) * 100:.1f}% "
        simulation_status += (
            f"| Elapsed Time: {elapsed_minutes}:{elapsed_seconds_str} min "
        )
        simulation_status += f"| Speed: {steps_per_second:.0f} step/s "
        simulation_status += f"| Time Left: {time_left_minutes}:{time_left_seconds} min"
        simulation_status += f"| Avg. iterations {average_iteration_tries:.1f}"
        log.information(simulation_status)
        return datetime.datetime.now()

    def get_std_results(self, results_data_frame: pd.DataFrame) -> pd.DataFrame:
        """Converts results into a pretty dataframe for post processing."""
        pd_timeline = pd.date_range(
            start=self._simulation_parameters.start_date,
            end=self._simulation_parameters.end_date,
            freq=f"{self._simulation_parameters.seconds_per_timestep}S",
        )[:-1]
        n_columns = results_data_frame.shape[1]
        results_data_frame.index = pd_timeline
        results_merged = pd.DataFrame()
        for i_column in range(n_columns):
            temp_df = pd.DataFrame(
                results_data_frame.values[:, i_column],
                index=pd_timeline,
                columns=[results_data_frame.columns[i_column]],
            )
            column_name1 = results_data_frame.columns[i_column]  # noqa
            column_name: str = cast(str, column_name1)
            if "Temperature" in column_name or "Percent" in column_name:
                temp_df = temp_df.resample("M").interpolate(method="linear")
            else:
                temp_df = temp_df.resample("M").sum()
            results_merged[temp_df.columns[0]] = temp_df.values[:, 0]
            results_merged.index = temp_df.index
        return results_merged
