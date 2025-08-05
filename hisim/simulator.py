"""The Simulator class forms the framework for all HiSim simulations.

It iterates over all components in each timestep until convergence and loops over all time steps.
"""
# clean
import os
import datetime
from typing import List, Tuple, Optional, Dict, Any, Union
import time
import pandas as pd

from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.component_wrapper import ComponentWrapper
from hisim import sim_repository
from hisim.postprocessing import postprocessing_main as pp
import hisim.component as cp
import hisim.dynamic_component as dcp
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim import utils
from hisim import postprocessingoptions
from hisim.loadtypes import Units
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum


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
        module_filename: str,
        my_simulation_parameters: Optional[SimulationParameters],
        setup_function: str = "setup_function",
        my_module_config: Optional[str] = None,
    ) -> None:
        """Initializes the simulator class and creates the result directory."""

        self._simulation_parameters: SimulationParameters
        if my_simulation_parameters is not None:
            self._simulation_parameters = my_simulation_parameters
            log.LOGGING_LEVEL = self._simulation_parameters.logging_level
        self.wrapped_components: List[ComponentWrapper] = []
        self.all_outputs: List[cp.ComponentOutput] = []

        self.setup_function = setup_function
        self.module_filename = module_filename
        self.module_directory = module_directory
        self.my_module_config = my_module_config
        self.simulation_repository = sim_repository.SimRepository()
        self.results_data_frame: pd.DataFrame
        self.iteration_logging_path: str = ""
        self.config_dictionary: Dict[str, Any] = {}

    def set_simulation_parameters(self, my_simulation_parameters: SimulationParameters) -> None:
        """Sets the simulation parameters and the logging level at the same time."""
        self._simulation_parameters = my_simulation_parameters
        if self._simulation_parameters is not None:
            log.LOGGING_LEVEL = self._simulation_parameters.logging_level

    def add_component(
        self,
        component: cp.Component,
        is_cachable: bool = False,
        connect_automatically: bool = False,
    ) -> None:
        """Adds component to simulator and wraps it up the output in the register."""
        if self._simulation_parameters is None:
            raise ValueError("Simulation Parameters were not initialized")
        # set the repository
        component.set_sim_repo(self.simulation_repository)

        # set the wrapper
        wrap = ComponentWrapper(component, is_cachable, connect_automatically=connect_automatically)
        wrap.register_component_outputs(self.all_outputs, wrapped_components_so_far=self.wrapped_components)
        self.wrapped_components.append(wrap)
        if component.component_name in self.config_dictionary:
            raise ValueError("duplicate component name : " + component.component_name)
        self.config_dictionary[component.component_name] = component.config

    @utils.measure_execution_time
    def connect_all_components(self) -> None:
        """Connects the inputs from every component to the corresponding outputs."""
        for wrapped_component in self.wrapped_components:
            wrapped_component.connect_inputs(self.all_outputs)

    @utils.measure_execution_time
    def prepare_calculation(self) -> None:
        """Connects the inputs from every component to the corresponding outputs."""
        for wrapped_component in self.wrapped_components:
            # check if component should be connected to default connections automatically
            if wrapped_component.connect_automatically is True:
                self.connect_everything_automatically(
                    source_component_list=[wp.my_component for wp in self.wrapped_components],
                    target_component=wrapped_component.my_component,
                )
            wrapped_component.prepare_calculation()

    def process_one_timestep(
        self, timestep: int, previous_stsv: cp.SingleTimeStepValues
    ) -> Tuple[cp.SingleTimeStepValues, int, bool]:
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
            if (
                iterative_tries > 2
                and postprocessingoptions.PostProcessingOptions.PROVIDE_DETAILED_ITERATION_LOGGING
                in self._simulation_parameters.post_processing_options
            ):
                myerr = stsv.get_differences_for_error_msg(previous_values, self.all_outputs)
                with open(self.iteration_logging_path, "a", encoding="utf-8") as filestream:
                    filestream.write(myerr + "\n")
            if iterative_tries > 10:
                force_convergence = True
            if iterative_tries > 100:
                list_of_changed_values = stsv.get_differences_for_error_msg(previous_values, self.all_outputs)
                raise ValueError("More than 100 tries in time step " + str(timestep) + "\n" + list_of_changed_values)
            # Copies actual values to previous variable
            previous_values.copy_values_from_other(stsv)
            iterative_tries += 1

        for wrapped_component in self.wrapped_components:
            wrapped_component.doublecheck(timestep, stsv)
        return (stsv, iterative_tries, force_convergence)

    def prepare_simulation_directory(self):
        """Prepares the simulation directory. Determines the filename if nothing is set."""

        if (
            self._simulation_parameters.result_directory is None
            or len(self._simulation_parameters.result_directory) == 0
            or self._simulation_parameters.result_directory == ""
        ):

            # check if result path is already set somewhere manually
            if ResultPathProviderSingleton().get_result_directory_name() is not None:
                self._simulation_parameters.result_directory = ResultPathProviderSingleton().get_result_directory_name()
                log.information(
                    "Using result directory: "
                    + self._simulation_parameters.result_directory
                    + " which is set manually."
                )
            else:
                # if not, build a flat result path itself
                ResultPathProviderSingleton().set_important_result_path_information(
                    module_directory=self.module_directory,
                    model_name=self.module_filename,
                    variant_name=None,
                    scenario_hash_string=None,
                    sorting_option=SortingOptionEnum.FLAT,
                )
                self._simulation_parameters.result_directory = ResultPathProviderSingleton().get_result_directory_name()
                log.information(
                    f"Using result directory:  {self._simulation_parameters.result_directory}"
                    + " which is set by the simulator."
                )
        
        if not os.path.isdir(self._simulation_parameters.result_directory):
            os.makedirs(self._simulation_parameters.result_directory, exist_ok=True)

        log.LOGGING_LEVEL = self._simulation_parameters.logging_level
        self.iteration_logging_path = os.path.join(
            self._simulation_parameters.result_directory, "Detailed_Iteration_Log.txt"
        )
        log.initialize_properly(self._simulation_parameters.result_directory)


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

        flagfile = os.path.join(self._simulation_parameters.result_directory, "finished.flag")
        if self._simulation_parameters.skip_finished_results and os.path.exists(flagfile):
            log.warning("Found " + flagfile + ". This calculation seems finished. Quitting.")
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
        log.information("Starting simulation for " + str(self._simulation_parameters.timesteps) + " timesteps")
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

            (
                resulting_stsv,
                iteration_tries,
                force_convergence,
            ) = self.process_one_timestep(step, stsv)
            stsv = cp.SingleTimeStepValues(number_of_outputs)
            # Accumulates iteration counter
            total_iteration_tries_since_last_msg += iteration_tries

            # Appends
            all_result_lines.append(resulting_stsv.values)
            del resulting_stsv
            # Calculates time execution
            elapsed = datetime.datetime.now() - lastmessage

            # For simulation longer than 5 seconds
            if elapsed.total_seconds() > 5 and step != 0:
                lastmessage = self.show_progress(
                    starttime,
                    step,
                    total_iteration_tries_since_last_msg,
                    last_step,
                    force_convergence,
                )
                last_step = step
                total_iteration_tries_since_last_msg = 0
        postprocessing_datatransfer = self.prepare_post_processing(all_result_lines, start_counter)
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
        self.results_data_frame = pd.DataFrame(data=all_result_lines, columns=colum_names)
        df_index = pd.date_range(
            start=self._simulation_parameters.start_date,
            end=self._simulation_parameters.end_date,
            freq=f"{self._simulation_parameters.seconds_per_timestep}S",
        )[:-1]
        self.results_data_frame.index = df_index
        end_counter = time.perf_counter()
        execution_time = end_counter - start_counter
        log.information(f"Simulation took {execution_time:1.2f}s.")

        if (
                postprocessingoptions.PostProcessingOptions.PLOT_MONTHLY_BAR_CHARTS in self._simulation_parameters.post_processing_options or
                postprocessingoptions.PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION in self._simulation_parameters.post_processing_options or
                postprocessingoptions.PostProcessingOptions.MAKE_OPERATION_RESULTS_FOR_WEBTOOL in self._simulation_parameters.post_processing_options or
                postprocessingoptions.PostProcessingOptions.MAKE_RESULT_JSON_FOR_WEBTOOL in self._simulation_parameters.post_processing_options or
                postprocessingoptions.PostProcessingOptions.EXPORT_MONTHLY_RESULTS in self._simulation_parameters.post_processing_options
        ):
            log.information("Preparing std results for post processing")
            (
                results_merged_cumulative,
                results_merged_monthly,
                results_merged_daily,
                results_merged_hourly,
            ) = self.get_std_results(self.results_data_frame)
        else:
            results_merged_cumulative = None
            results_merged_monthly = None
            results_merged_daily = None
            results_merged_hourly = None

        ppdt = PostProcessingDataTransfer(
            results=self.results_data_frame,
            all_outputs=self.all_outputs,
            simulation_parameters=self._simulation_parameters,
            wrapped_components=self.wrapped_components,
            mode=1,
            setup_function=self.setup_function,
            module_filename=self.module_filename,
            my_module_config=self.my_module_config,
            execution_time=execution_time,
            results_monthly=results_merged_monthly,
            results_cumulative=results_merged_cumulative,
            results_hourly=results_merged_hourly,
            results_daily=results_merged_daily,
        )
        log.information("Finished preparing post processing")
        return ppdt

    def show_progress(
        self,
        starttime: datetime.datetime,
        step: int,
        total_iteration_tries: int,
        last_step: int,
        force_covergence: bool,
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
        time_elapsed = datetime.timedelta(seconds=(self._simulation_parameters.timesteps - step) / steps_per_second)
        time_left_minutes, time_left_seconds = divmod(time_elapsed.seconds, 60)
        time_left_seconds = str(time_left_seconds).zfill(2)  # type: ignore
        simulation_status = f"Simulating... {(step / self._simulation_parameters.timesteps) * 100:.1f}% "
        simulation_status += f"| Elapsed Time: {elapsed_minutes}:{elapsed_seconds_str} min "
        simulation_status += f"| Speed: {steps_per_second:.0f} step/s "
        simulation_status += f"| Time Left: {time_left_minutes}:{time_left_seconds} min"
        simulation_status += f"| Avg. iterations {average_iteration_tries:.1f}"
        if force_covergence:
            simulation_status += " (forced)"
        log.information(simulation_status)
        return datetime.datetime.now()

    @utils.measure_execution_time
    def get_std_results(
        self, results_data_frame: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Converts results into a pretty dataframe for post processing."""

        units_mean = {
            Units.CELSIUS,
            Units.KELVIN,
            Units.ANY,
            Units.METER_PER_SECOND,
            Units.DEGREES,
            Units.WATT,
            Units.KILOWATT,
            Units.WATT_PER_SQUARE_METER,
            Units.KG_PER_SEC,
            Units.PERCENT,
            Units.PASCAL,
        }

        monthly_frames = []
        daily_frames = []
        cumulative_data = {}
        hourly_frames = []

        use_hourly_resample = self._simulation_parameters.seconds_per_timestep != 3600

        for i, column_name in enumerate(results_data_frame.columns):
            log.debug(f"Processing column {i + 1}/{len(results_data_frame.columns)} - {column_name}")
            col_data = results_data_frame.iloc[:, i]
            unit = self.all_outputs[i].unit

            if unit in units_mean:
                monthly = col_data.resample("M").mean()
                daily = col_data.resample("D").mean()
                hourly = col_data.resample("60T").mean() if use_hourly_resample else col_data
                cumulative = col_data.mean()
            else:
                monthly = col_data.resample("M").sum()
                daily = col_data.resample("D").sum()
                hourly = col_data.resample("60T").sum() if use_hourly_resample else col_data
                cumulative = col_data.sum()

            monthly_frames.append(monthly.rename(column_name))
            daily_frames.append(daily.rename(column_name))
            hourly_frames.append(hourly.rename(column_name))
            cumulative_data[column_name] = cumulative

        # Combine at once to avoid per-column index assignment
        results_merged_monthly = pd.concat(monthly_frames, axis=1)
        results_merged_daily = pd.concat(daily_frames, axis=1)
        results_merged_hourly = pd.concat(hourly_frames, axis=1)
        results_merged_cumulative = pd.DataFrame([cumulative_data])

        return (
            results_merged_cumulative,
            results_merged_monthly,
            results_merged_daily,
            results_merged_hourly,
        )

    def connect_everything_automatically(
        self,
        source_component_list: Union[List[cp.Component], List[dcp.DynamicComponent]],
        target_component: Union[cp.Component, dcp.DynamicComponent],
    ) -> None:
        """Connect chosen target component in the sytem setups automatically based on its default connections."""

        # prepare the target components' default connection lists
        target_default_connection_dict: Union[
            Dict[str, List[cp.ComponentConnection]],
            Dict[str, List[dcp.DynamicComponentConnection]],
        ]

        # check if target component is a normal or a dynamic component and get all default connections
        if isinstance(target_component, dcp.DynamicComponent):
            target_default_connection_dict = target_component.dynamic_default_connections

        elif isinstance(target_component, cp.Component) and not isinstance(target_component, dcp.DynamicComponent):
            target_default_connection_dict = target_component.default_connections

        else:
            raise TypeError(
                f"Type {type(target_component)} of target_component should be Component or Dynamic Component."
            )

        # check if target component has any default connections (otherwise automatic connection cannot be made)
        if bool(target_default_connection_dict) is True:
            # check if at least one source_component is in the target default connections
            if (
                any(
                    source_component.get_classname() in target_default_connection_dict
                    for source_component in source_component_list
                )
                is False
            ):
                raise KeyError(
                    f"No component in the system setup matches the default connections of {target_component.component_name}."
                )

            # go through all registered components
            for source_component in source_component_list:
                source_component_classname = source_component.get_classname()

                # if the source components' classname is found in the target components' default connection dict, a connection is made
                if source_component_classname in target_default_connection_dict.keys():
                    if isinstance(target_component, dcp.DynamicComponent):
                        dynamic_connections = target_component.get_dynamic_default_connections(
                            source_component=source_component
                        )

                        target_component.connect_with_dynamic_connections_list(
                            dynamic_component_connections=dynamic_connections
                        )

                    if isinstance(target_component, cp.Component) and not isinstance(
                        target_component, dcp.DynamicComponent
                    ):
                        connections = target_component.get_default_connections(source_component=source_component)
                        target_component.connect_with_connections_list(connections=connections)
        else:
            raise KeyError(
                f"Automatic connection does not work for {target_component.component_name} because no default connections were found. "
                + "Please check if a connection is needed and if yes, create the missing default connection in your component."
            )
