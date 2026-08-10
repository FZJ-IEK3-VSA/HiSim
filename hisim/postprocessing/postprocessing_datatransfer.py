"""Data Transfer Object to get all the result data to the post processing."""

from typing import Any, Dict, List, Optional
import pandas as pd

from hisim.component import ComponentOutput
from hisim.component_wrapper import ComponentWrapper
from hisim.simulationparameters import SimulationParameters


#: Number of seconds in one hour. Used to convert power [W] to energy [Wh].
SECONDS_PER_HOUR: int = 3600


class PostProcessingDataTransfer:  # noqa: too-few-public-methods
    """Bundles simulation results and metadata for post-processing routines.

    Holds references (not copies) to the simulation's result DataFrames,
    component outputs, and configuration. Callers must avoid in-place
    mutation of the attached DataFrames and lists, as changes are visible
    to all downstream consumers sharing this instance.
    """

    def __init__(
        self,
        results: pd.DataFrame,
        all_outputs: List[ComponentOutput],
        simulation_parameters: SimulationParameters,
        wrapped_components: List[ComponentWrapper],
        mode: int,
        setup_function: str,
        module_filename: str,
        module_config: Optional[str],
        execution_time_in_s: float,
        results_monthly: Optional[pd.DataFrame],
        results_hourly: Optional[pd.DataFrame],
        results_cumulative: Optional[pd.DataFrame],
        results_daily: Optional[pd.DataFrame],
        kpi_collection_dict: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize a PostProcessingDataTransfer instance.

        Args:
            results: DataFrame containing all simulation time-series results.
            all_outputs: List of ComponentOutput objects produced by the simulation.
            simulation_parameters: SimulationParameters defining the simulation configuration.
            wrapped_components: List of ComponentWrapper instances used in the simulation.
            mode: Integer identifier for the simulation mode.
            setup_function: Name of the setup function used to configure the simulation.
            module_filename: Filename of the Python module containing the setup function.
            module_config: Optional string with module-specific configuration.
            execution_time_in_s: Total wall-clock time of the simulation run in seconds.
            results_monthly: Optional monthly-aggregated results DataFrame.
            results_hourly: Optional hourly-aggregated results DataFrame.
            results_cumulative: Optional cumulative results DataFrame.
            results_daily: Optional daily-aggregated results DataFrame.
            kpi_collection_dict: Optional dictionary of KPI name to KPI value mappings.
                Defaults to an empty dict if not provided.
        """
        if kpi_collection_dict is None:
            kpi_collection_dict = {}
        # Johanna Ganglbauer: time correction factor is applied in postprocessing to sum over power values and convert them to energy.
        # Unit: hours per timestep (seconds_per_timestep / SECONDS_PER_HOUR).
        # Multiplying a sum of power values [W] by this factor yields energy [Wh].
        self.time_correction_factor_in_hours_per_timestep: float = (
            simulation_parameters.seconds_per_timestep / SECONDS_PER_HOUR
        )
        self.results: pd.DataFrame = results
        self.all_outputs: List[ComponentOutput] = all_outputs
        self.simulation_parameters: SimulationParameters = simulation_parameters
        self.wrapped_components: List[ComponentWrapper] = wrapped_components
        self.mode: int = mode
        self.setup_function: str = setup_function
        self.module_filename: str = module_filename
        self.module_config: Optional[str] = module_config
        self.execution_time_in_s: float = execution_time_in_s
        self.results_monthly: Optional[pd.DataFrame] = results_monthly
        self.results_hourly: Optional[pd.DataFrame] = results_hourly
        self.results_cumulative: Optional[pd.DataFrame] = results_cumulative
        self.results_daily: Optional[pd.DataFrame] = results_daily
        self.post_processing_options: List[int] = simulation_parameters.post_processing_options
        self.kpi_collection_dict: Dict[str, Any] = kpi_collection_dict
