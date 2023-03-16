""" Data Transfer Object to get all the result data to the post processing. """

# clean
from typing import Any, List

from hisim import log
from hisim.component_wrapper import ComponentWrapper
from hisim.simulationparameters import SimulationParameters


class PostProcessingDataTransfer:  # noqa: too-few-public-methods

    """Data class for transfering the result data to this class."""

    def __init__(
        self,
        results: Any,
        all_outputs: Any,
        simulation_parameters: SimulationParameters,
        wrapped_components: List[ComponentWrapper],
        mode: Any,
        setup_function: Any,
        execution_time: Any,
        results_monthly: Any,
    ) -> None:
        """Initializes the values."""
        # Johanna Ganglbauer: time correction factor is applied in postprocessing to sum over power values and convert them to energy
        self.time_correction_factor = simulation_parameters.seconds_per_timestep / 3600
        self.results = results
        self.all_outputs = all_outputs
        self.simulation_parameters = simulation_parameters
        self.wrapped_components: List[ComponentWrapper] = wrapped_components
        self.mode = mode
        self.setup_function = setup_function
        self.execution_time = execution_time
        self.results_monthly = results_monthly
        self.post_processing_options = simulation_parameters.post_processing_options

        log.information(
            "Selected "
            + str(len(self.post_processing_options))
            + " post processing options:"
        )
        for option in self.post_processing_options:
            log.information("Selected post processing option: " + str(option))
