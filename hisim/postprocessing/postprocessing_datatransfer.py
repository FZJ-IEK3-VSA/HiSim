"""Data Transfer Object to get all the result data to the post processing."""

from typing import List, Dict, Optional
import pandas as pd

from hisim import log
from hisim.component import ComponentOutput
from hisim.component_wrapper import ComponentWrapper
from hisim.simulationparameters import SimulationParameters


class PostProcessingDataTransfer:  # noqa: too-few-public-methods
    """Data class for transferring the result data to this class."""

    def __init__(
        self,
        results: pd.DataFrame,
        all_outputs: List[ComponentOutput],
        simulation_parameters: SimulationParameters,
        wrapped_components: List[ComponentWrapper],
        mode: int,
        setup_function: str,
        module_filename: str,
        my_module_config: Optional[str],
        execution_time: float,
        results_monthly: Optional[pd.DataFrame],
        results_hourly: Optional[pd.DataFrame],
        results_cumulative: Optional[pd.DataFrame],
        results_daily: Optional[pd.DataFrame],
        kpi_collection_dict: Optional[Dict] = None,
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
            my_module_config: Optional string with module-specific configuration.
            execution_time: Total wall-clock time of the simulation run in seconds.
            results_monthly: Optional monthly-aggregated results DataFrame.
            results_hourly: Optional hourly-aggregated results DataFrame.
            results_cumulative: Optional cumulative results DataFrame.
            results_daily: Optional daily-aggregated results DataFrame.
            kpi_collection_dict: Optional dictionary of KPI name to KPI value mappings.
                Defaults to an empty dict if not provided.
        """
        if kpi_collection_dict is None:
            kpi_collection_dict = {}
        # Johanna Ganglbauer: time correction factor is applied in postprocessing to sum over power values and convert them to energy
        self.time_correction_factor = simulation_parameters.seconds_per_timestep / 3600
        self.results = results
        self.all_outputs = all_outputs
        self.simulation_parameters = simulation_parameters
        self.wrapped_components: List[ComponentWrapper] = wrapped_components
        self.mode = mode
        self.setup_function = setup_function
        self.module_filename = module_filename
        self.my_module_config = my_module_config
        self.execution_time = execution_time
        self.results_monthly = results_monthly
        self.results_hourly = results_hourly
        self.results_cumulative = results_cumulative
        self.results_daily = results_daily
        self.post_processing_options = simulation_parameters.post_processing_options
        self.kpi_collection_dict = kpi_collection_dict

        log.information(f"Selected {len(self.post_processing_options)} post processing options:")
        for option in self.post_processing_options:
            log.information(f"Selected post processing option: {option}")
