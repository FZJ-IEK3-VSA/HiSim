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
import hisim.component as cp
import hisim.dynamic_component as dcp
from hisim import log
from hisim.economics.facts import (
    CostRelevance,
    UndeclaredCostRelevanceError,
    UnpriceableComponentError,
)
from hisim.simulationparameters import SimulationParameters
from hisim import utils
from hisim import postprocessingoptions
from hisim.loadtypes import UNITS_USING_MEAN_AGGREGATION
from hisim.result_path_provider import ResultPathProviderSingleton


def _has_cost_facts_source(component_class: type) -> bool:
    """Whether anything can produce `ComponentCostFacts` for this class (cost_spec.md §9.1).

    Mirrors the precedence `adapter.extract_cost_facts` applies at postprocessing time, so the
    pre-run refusal and the D7 abort can never disagree about which classes have a facts source:
    a class that implements `get_cost_facts` itself wins, and the compatibility table is consulted
    only when it has not.

    "Implements it itself" is "the attribute is not the one `Component` defines" rather than a
    lookup in the class body, so a component that inherits a working hook from a component base
    class of its own counts as having one — which it does, at run time, since that is the method
    the adapter will call. The lookup goes through `getattr` with a default for the same reason the
    adapter's does: a class carrying no such attribute at all is a class with no hook, not a crash.

    The adapter is imported here rather than at module level: `hisim.economics.adapter` reaches
    into the KPI structures and the load types, and the simulator is imported by everything, so
    the dependency stays where the one function that needs it can see it.

    Args:
        component_class: The registered component's class.

    Returns:
        True when the class implements the hook or the adapter table knows its name.
    """
    from hisim.economics.adapter import FactsExtractors  # noqa: E402  (see the docstring)

    hook = getattr(component_class, "get_cost_facts", None)
    if hook is not None and hook is not cp.Component.get_cost_facts:
        return True
    return component_class.__name__ in FactsExtractors.BY_CLASS_NAME


__authors__ = "Noah Pflugradt, Vitor Hugo Bellotto Zago, Maximillian Hillen"
__copyright__ = "Copyright 2020-2022, FZJ-IEK-3"
__license__ = "MIT"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "production"


class Simulator:

    """Core class of HiSim that orchestrates the simulation lifecycle.

    Manages component registration, input/output connections, timestep iteration
    with convergence checking, and post-processing of results.
    """

    @utils.measure_execution_time
    def __init__(
        self,
        module_directory: str,
        module_filename: str,
        my_simulation_parameters: Optional[SimulationParameters],
        setup_function: str = "setup_function",
        my_module_config: Optional[str] = None,
        force_log_connections: bool = False,
    ) -> None:
        """Initializes the simulator with module path, parameters, and setup config.

        Args:
            module_directory: Path to the directory containing the simulation module.
            module_filename: Filename of the simulation module.
            my_simulation_parameters: Simulation parameters controlling timesteps,
                duration, etc. May be None if set later via set_simulation_parameter.
            setup_function: Name of the setup function to call within the module.
            my_module_config: Optional module configuration identifier.
            force_log_connections: If True, forces logging of component connections.
        """

        # When set, the simulation parameters used for this run always log component
        # connections, so component_connections.json is written for post-processing/
        # debugging. Enabled by the Python entry point to mirror the JSON path.
        self._force_log_connections: bool = force_log_connections
        self._simulation_parameters: SimulationParameters
        if my_simulation_parameters is not None:
            if self._force_log_connections:
                my_simulation_parameters.log_connections = True
            self._simulation_parameters = my_simulation_parameters
            log.logger.logging_level = self._simulation_parameters.logging_level
        self.wrapped_components: List[ComponentWrapper] = []
        self.all_outputs: List[cp.ComponentOutput] = []

        self.setup_function = setup_function
        self.module_filename = module_filename
        self.module_directory = module_directory
        self.my_module_config = my_module_config
        #: Name of the scenario this run represents, carried into post-processing as the
        #: pyam "scenario" column and as the ``name`` of ``scenario.json``. A Python setup
        #: function sets it on the simulator it is handed (the building-sizer setups write
        #: their scenario hash string here); a declarative run gets the energy-system file's
        #: own ``name``, followed by the option each variant selected when the file has any.
        #: Left empty here, and only here: a run nobody named is named after its module file
        #: by :meth:`prepare_post_processing`, so that no run reaches a scenario evaluation
        #: anonymous and two unnamed runs never merge into one nameless row.
        self.scenario_name: str = ""
        #: One-line description of the run. The Python entry point takes it from the first
        #: line of the setup file — a docstring or a comment, triple quotes stripped — a
        #: declarative run from the energy-system file's ``description`` field.
        #: Post-processing writes it into ``scenario.json``.
        self.description: str = ""
        self.simulation_repository = sim_repository.SimRepository()
        self.results_data_frame: pd.DataFrame
        self.iteration_logging_path: str = ""
        self.config_dictionary: Dict[str, Any] = {}

    def set_simulation_parameters(self, my_simulation_parameters: SimulationParameters) -> None:
        """Sets the simulation parameters and the logging level at the same time.

        Args:
            my_simulation_parameters: The simulation parameters to use for this run.
        """
        self._simulation_parameters = my_simulation_parameters
        if self._simulation_parameters is not None:
            if self._force_log_connections:
                self._simulation_parameters.log_connections = True
            log.logger.logging_level = self._simulation_parameters.logging_level

    def get_simulation_parameters(self) -> SimulationParameters:
        """Returns the simulation parameters for exporting them to JSON.

        Returns:
            The SimulationParameters instance used by this simulator.
        """
        return self._simulation_parameters

    @property
    def simulation_parameters(self) -> SimulationParameters:
        """The parameters this simulator runs with — the public way to read them.

        A system setup regularly needs a figure the parameters carry, above all the result
        directory the simulator resolved for the run, and had no public accessor phrased as an
        attribute: the reference setups reached into `_simulation_parameters` behind a
        `noqa: SLF001`, which is precisely the habit reference material should not teach.
        Read-only on purpose — `set_simulation_parameters` also adjusts the logging level and the
        connection logging, so assigning the attribute would skip half of what setting the
        parameters means. The object itself is not a copy, so mutating a field on it (as the HPC
        harness does with `result_directory`) still works.
        """
        return self._simulation_parameters

    def add_component(
        self,
        component: cp.Component,
        is_cachable: bool = False,
        connect_automatically: bool = False,
    ) -> None:
        """Adds a component to the simulator and registers its outputs.

        Args:
            component: The component instance to add.
            is_cachable: Whether the component's results can be cached.
            connect_automatically: Whether to attempt automatic default connections.

        Raises:
            ValueError: If simulation parameters are not initialized or a duplicate
                component name is added.
        """
        if self._simulation_parameters is None:
            raise ValueError("Simulation Parameters were not initialized")
        # ensure result directory exists before any connect_input calls log to it
        if not self._simulation_parameters.result_directory:
            self.prepare_simulation_directory()
        # set the repository
        component.set_sim_repo(self.simulation_repository)

        # set the wrapper
        wrap = ComponentWrapper(component, is_cachable, connect_automatically=connect_automatically)
        wrap.register_component_outputs(self.all_outputs, wrapped_components_so_far=self.wrapped_components)
        self.wrapped_components.append(wrap)
        if component.component_name in self.config_dictionary:
            raise ValueError(f"duplicate component name : {component.component_name}")
        self.config_dictionary[component.component_name] = component.config

    @utils.measure_execution_time
    def connect_all_components(self) -> None:
        """Connects the inputs from every component to the corresponding outputs."""
        for wrapped_component in self.wrapped_components:
            wrapped_component.connect_inputs(self.all_outputs)

    @utils.measure_execution_time
    def prepare_calculation(self) -> None:
        """Prepares all components for the simulation run.

        For each wrapped component, if automatic connection is enabled, attempts to
        connect it to matching source components via default connections. Then calls
        `prepare_calculation` on every wrapped component so they can initialise
        internal state before the timestep loop begins.
        """
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
                raise ValueError(f"More than 100 tries in time step {timestep}\n{list_of_changed_values}")
            # Copies actual values to previous variable
            previous_values.copy_values_from_other(stsv)
            iterative_tries += 1

        for wrapped_component in self.wrapped_components:
            wrapped_component.doublecheck(timestep, stsv)
        return (stsv, iterative_tries, force_convergence)

    def prepare_simulation_directory(self):
        """Prepares the simulation directory, creating it if necessary.

        If no result directory is set, uses one from ResultPathProviderSingleton or
        builds a flat result path.

        The path provider is a process-wide singleton, so a second simulation run in the same
        process would otherwise find the *first* run's directory still configured there and adopt
        it — silently overwriting the first run's artifacts, which is what happens when one script
        runs a reference setup and then a variant. A configuration the provider marks as
        simulator-derived therefore belongs to a previous ``Simulator`` and is dropped here, while
        a directory a caller chose deliberately (a test, the HPC harness, RenoVisor) is still
        honoured as it always was.

        Raises:
            ValueError: If the result path provider does not return a directory.
        """

        if (
            self._simulation_parameters.result_directory is None
            or len(self._simulation_parameters.result_directory) == 0
            or self._simulation_parameters.result_directory == ""
        ):

            if ResultPathProviderSingleton().configured_by_simulator:
                # Left over from an earlier in-process run: it names that run's setup module and
                # carries that run's timestamp. Dropping the instance also refreshes the timestamp.
                log.information(
                    "Discarding the result path of a previous in-process simulation run "
                    f"({ResultPathProviderSingleton().get_result_directory_name()}); this run "
                    "derives its own."
                )
                ResultPathProviderSingleton.reset()
            # check if result path is already set somewhere manually
            result_directory = ResultPathProviderSingleton().get_result_directory_name()
            if result_directory is not None:
                self._simulation_parameters.result_directory = result_directory
                log.information(
                    f"Using result directory: {self._simulation_parameters.result_directory} which is set manually."
                )
            else:
                # if not, build a flat result path itself
                ResultPathProviderSingleton().configure_for_simulator_run(
                    module_directory=self.module_directory, model_name=self.module_filename
                )
                result_directory = ResultPathProviderSingleton().get_result_directory_name()
                if result_directory is None:
                    raise ValueError("Result path provider did not return a result directory.")
                self._simulation_parameters.result_directory = result_directory
                log.information(
                    f"Using result directory:  {self._simulation_parameters.result_directory}"
                    + " which is set by the simulator."
                )

        if not os.path.isdir(self._simulation_parameters.result_directory):
            os.makedirs(self._simulation_parameters.result_directory, exist_ok=True)

        self.iteration_logging_path = os.path.join(
            self._simulation_parameters.result_directory, "Detailed_Iteration_Log.txt"
        )

    def check_cost_declarations(self) -> None:
        """Refuses a lifecycle-cost run this fleet's components cannot be described for (§9.1/§9.2).

        The completeness check cost_spec.md §9.2 asks for "at simulation start, not end", and this
        method answers it for the two defects that are visible without the cost database:

        1. a class that declares no `cost_relevance` at all and therefore keeps the `UNDECLARED`
           base-class default, and
        2. a class that declares `PRICED` while nothing can produce facts for it — neither a
           `get_cost_facts` implementation of its own nor an entry in
           `adapter.FactsExtractors.BY_CLASS_NAME`.

        Both end the same way in postprocessing: the bridge turns them into unresolved subjects and
        the D7 check aborts the evaluation. Finding that out there means a year-long simulation runs
        for hours and then dies without producing the cost report it was started for, so both are
        caught here instead. The second check mirrors the precedence `adapter.extract_cost_facts`
        applies — hook first, table second — so this refusal and that one can never disagree about
        which classes have a facts source.

        What still belongs to the bridge, because it needs data this method has no business
        loading: whether the facts a source produces can actually be *priced* — an asset class with
        no `devices_<COUNTRY>.json` row, a meter whose configured fuel maps to no carrier, an
        extractor that returns None for this particular configuration. Those stay with the D7
        check.

        Does nothing unless `PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS` or
        `LIFECYCLE_COST_REPORT` was requested: components are free to be undeclared in a run that
        never asks what anything costs.

        Raises:
            UndeclaredCostRelevanceError: If lifecycle costs were requested and at least one
                registered component's class declares no `cost_relevance`. The message names
                every offending class and the module it lives in. It is a `ValueError`, so a
                caller catching `run_all_timesteps`' documented refusal type still catches it.
            UnpriceableComponentError: If a registered component's class declares `PRICED` and has
                neither its own `get_cost_facts` nor an adapter-table entry. Also a `ValueError`,
                for the same reason. Reported separately from the undeclared classes because the
                fix is a different one.
        """
        options = self._simulation_parameters.post_processing_options
        wanted = (
            postprocessingoptions.PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS in options
            or postprocessingoptions.PostProcessingOptions.LIFECYCLE_COST_REPORT in options
        )
        if not wanted:
            return
        undeclared: List[type] = []
        unpriceable: List[type] = []
        for wrapped_component in self.wrapped_components:
            component_class = type(wrapped_component.my_component)
            # `getattr` with the default mirrors `adapter.effective_cost_relevance`: a class that
            # does not carry the attribute at all is undeclared, not a crash.
            relevance = getattr(component_class, "cost_relevance", CostRelevance.UNDECLARED)
            if relevance is CostRelevance.UNDECLARED:
                if component_class not in undeclared:
                    undeclared.append(component_class)
                continue
            if relevance is CostRelevance.PRICED and not _has_cost_facts_source(component_class):
                if component_class not in unpriceable:
                    unpriceable.append(component_class)
        if undeclared:
            raise UndeclaredCostRelevanceError(undeclared)
        if unpriceable:
            raise UnpriceableComponentError(unpriceable)

    # @profile
    # @utils.measure_execution_time
    def run_all_timesteps(self) -> None:
        """Performs all timesteps and saves results for post-processing.

        Raises:
            ValueError: If simulation parameters are not initialized, no components
                are defined, post-processing data transfer is None, or lifecycle costs were
                requested while some component declares no `cost_relevance`
                (`check_cost_declarations`).
        """
        # Error Tests
        # Test if all parameters were initialized
        if self._simulation_parameters is None:
            raise ValueError("Simulation Parameters were not initialized")

        # Tests if wrapper has any components at all
        if len(self.wrapped_components) == 0:
            raise ValueError("Not a single component was defined. Quitting.")

        # A lifecycle-cost run needs every component to have declared a cost role; refuse now
        # rather than after hours of simulation (cost_spec.md §9.2).
        self.check_cost_declarations()

        # prepare logging and simulation directory
        self.prepare_simulation_directory()
        log.logger.setup(self._simulation_parameters.result_directory)

        flagfile = os.path.join(self._simulation_parameters.result_directory, "finished.flag")
        if self._simulation_parameters.skip_finished_results and os.path.exists(flagfile):
            log.warning(f"Found {flagfile}. This calculation seems finished. Quitting.")
            return
        # Starts time counter
        start_counter = time.perf_counter()
        self.prepare_calculation()
        # Connects all components
        self.connect_all_components()
        log.information(
            f"finished connecting all components. A total of {len(self.wrapped_components)} "
            f"components were defined. They have a total of {len(self.all_outputs)} outputs."
        )
        all_result_lines = []
        log.information(f"Starting simulation for year {self._simulation_parameters.year}")
        log.information(f"Starting simulation for {self._simulation_parameters.timesteps} timesteps")
        lastmessage = datetime.datetime.now()
        last_step: int = 0
        starttime = datetime.datetime.now()
        total_iteration_tries_since_last_msg = 0

        # Creates empty list with values to get started
        number_of_outputs = len(self.all_outputs)
        stsv = cp.SingleTimeStepValues(number_of_outputs)

        for step in range(self._simulation_parameters.timesteps):
            (
                resulting_stsv,
                iteration_tries,
                force_convergence,
            ) = self.process_one_timestep(step, stsv)
            # Comment this out to always begin the convergence process with the previously converged state
            # stsv = cp.SingleTimeStepValues(number_of_outputs)

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

        from hisim.postprocessing import postprocessing_main as pp  # pylint: disable=import-outside-toplevel

        my_post_processor = pp.PostProcessor()
        my_post_processor.run(ppdt=postprocessing_datatransfer, simulator=self)
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
        """Assembles simulation results into a DataFrame and prepares data for post-processing.

        Builds a pandas DataFrame from simulation outputs, assigns a datetime index based on
        simulation start/end dates and timestep size, and optionally computes monthly, daily,
        hourly, and cumulative aggregations. Returns a PostProcessingDataTransfer object
        containing all results and metadata.

        Args:
            all_result_lines: List of result arrays, one per timestep.
            start_counter: High-resolution time from before simulation started, used to compute execution time.

        Returns:
            PostProcessingDataTransfer: Object bundling results, outputs, parameters, and timing for post-processing.
        """
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
            log.debug(f"Output column: {column_name}")
        self.results_data_frame = pd.DataFrame(data=all_result_lines, columns=colum_names)
        df_index = pd.date_range(
            start=self._simulation_parameters.start_date,
            end=self._simulation_parameters.end_date,
            freq=f"{self._simulation_parameters.seconds_per_timestep}s",
        )[:-1]
        self.results_data_frame.index = df_index
        end_counter = time.perf_counter()
        execution_time = end_counter - start_counter
        log.information(f"Simulation took {execution_time:1.2f}s.")

        if (
                postprocessingoptions.PostProcessingOptions.PLOT_MONTHLY_BAR_CHARTS in self._simulation_parameters.post_processing_options or
                postprocessingoptions.PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION in self._simulation_parameters.post_processing_options or
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

        # A run nobody named is named after the module it ran: an empty scenario column is a
        # row no scenario evaluation can tell apart from the next unnamed run's.
        scenario_name = self.scenario_name or self.module_filename
        ppdt = PostProcessingDataTransfer(
            results=self.results_data_frame,
            all_outputs=self.all_outputs,
            simulation_parameters=self._simulation_parameters,
            wrapped_components=self.wrapped_components,
            mode=1,
            setup_function=self.setup_function,
            module_filename=self.module_filename,
            module_config=self.my_module_config,
            execution_time_in_s=execution_time,
            scenario_name=scenario_name,
            description=self.description,
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
        """Logs a progress message with elapsed time, speed, and time estimate.

        Args:
            starttime: When the simulation started.
            step: Current timestep index.
            total_iteration_tries: Convergence iterations since last progress message.
            last_step: Timestep index of the last progress message.
            force_covergence: Whether convergence was forced in the current timestep.

        Returns:
            The current datetime, to use as the timestamp for the next message.
        """
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
        """Converts results into aggregated DataFrames for post-processing.

        Computes monthly, daily, hourly, and cumulative aggregations, using mean
        aggregation for units in UNITS_USING_MEAN_AGGREGATION and sum otherwise.

        Args:
            results_data_frame: DataFrame with raw per-timestep simulation results.

        Returns:
            A tuple of (cumulative, monthly, daily, hourly) DataFrames.
        """

        units_mean = UNITS_USING_MEAN_AGGREGATION
        use_hourly_resample = self._simulation_parameters.seconds_per_timestep != 3600

        # Split columns once by aggregation rule and resample each sub-DataFrame
        # in a single call. ``mean``/``sum`` are column-wise reductions, so this
        # is numerically identical to the previous per-column resampling but
        # issues ~6 resample calls instead of ~3 * n_columns (GitLab #1687).
        # Selection and recombination are positional (``iloc``) so the result is
        # independent of column-label uniqueness.
        column_count = len(results_data_frame.columns)
        mean_indices = [
            i for i in range(column_count) if self.all_outputs[i].unit in units_mean
        ]
        sum_indices = [
            i for i in range(column_count) if self.all_outputs[i].unit not in units_mean
        ]
        mean_df = results_data_frame.iloc[:, mean_indices]
        sum_df = results_data_frame.iloc[:, sum_indices]
        log.debug(
            f"get_std_results: {len(mean_indices)} mean-aggregated and "
            f"{len(sum_indices)} sum-aggregated of {column_count} columns"
        )

        def _resample_group(frame: pd.DataFrame, freq: str, how: str) -> pd.DataFrame:
            # resample on a column-less frame is valid but pointless; skip it to
            # avoid creating a spurious (empty) index that misaligns on concat.
            if frame.shape[1] == 0:
                return pd.DataFrame(columns=frame.columns)
            return frame.resample(freq).agg(how)

        monthly_mean = _resample_group(mean_df, "ME", "mean")
        monthly_sum = _resample_group(sum_df, "ME", "sum")
        daily_mean = _resample_group(mean_df, "D", "mean")
        daily_sum = _resample_group(sum_df, "D", "sum")
        if use_hourly_resample:
            hourly_mean = _resample_group(mean_df, "60min", "mean")
            hourly_sum = _resample_group(sum_df, "60min", "sum")
        else:
            # No resampling needed when the simulation already runs on an hourly
            # grid; pass the raw slices through unchanged.
            hourly_mean = mean_df
            hourly_sum = sum_df

        # Cumulative scalars (``.mean()``/``.sum()``) are cheap column-wise
        # reductions; keep them per-column to preserve the previous scalar
        # aggregation (and dict-keyed column order) exactly.
        cumulative_data: Dict[Any, Any] = {}
        for i, column_name in enumerate(results_data_frame.columns):
            col_data = results_data_frame.iloc[:, i]
            if self.all_outputs[i].unit in units_mean:
                cumulative_data[column_name] = col_data.mean()
            else:
                cumulative_data[column_name] = col_data.sum()

        # Recombine the mean/sum groups in the original column order. After the
        # concat the columns sit in [mean_indices..., sum_indices...] order, so
        # map every original index back to its concatenated position and select
        # positionally.
        def _combine_in_order(mean_frame: pd.DataFrame, sum_frame: pd.DataFrame) -> pd.DataFrame:
            combined = pd.concat([mean_frame, sum_frame], axis=1)
            position_in_combined: Dict[int, int] = {}
            for position, original_index in enumerate(mean_indices):
                position_in_combined[original_index] = position
            for position, original_index in enumerate(sum_indices):
                position_in_combined[original_index] = len(mean_indices) + position
            reorder = [position_in_combined[original_index] for original_index in range(column_count)]
            return combined.iloc[:, reorder]

        results_merged_monthly = _combine_in_order(monthly_mean, monthly_sum)
        results_merged_daily = _combine_in_order(daily_mean, daily_sum)
        results_merged_hourly = _combine_in_order(hourly_mean, hourly_sum)
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
        """Connects a target component to source components via default connections.

        Args:
            source_component_list: Candidate source components to connect from.
            target_component: The component whose default connections to resolve.

        Raises:
            TypeError: If target_component is not a Component or DynamicComponent.
            KeyError: If no source matches the target's default connections, or the
                target has no default connections defined.
        """

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
