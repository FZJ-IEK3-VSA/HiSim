"""The check after the time loop that every result column holds only finite values.

The components do not check on every pass that what they compute is finite; the post-processing checks the whole
result table once (:mod:`hisim.postprocessing.finite_results`). These tests run a one-day hourly simulation with one
stub component, ``NanSource``, that publishes ``100 + timestep`` and, when asked, NaN at one known step. A second
output, declared ``may_be_non_finite``, is NaN in every step and must never fail the run.
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, List, Optional

import numpy as np
import pandas as pd
import pytest
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import ComponentOutput, PhysicsComponent, SingleTimeStepValues
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.postprocessing.finite_results import FiniteResultsCheck, NonFiniteResultsError
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


@dataclass_json
@dataclass
class NanSourceConfig(ConfigBase):
    """Configuration of the stub source: its identity and the one step, if any, at which it publishes NaN.

    The config belongs to a test double, so it names its main class itself.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the stub source."""
        return NanSource.get_full_classname()

    component_id: ComponentID
    nan_step: Optional[int] = None


class NanSource(PhysicsComponent):
    """Publishes ``100 + timestep``, NaN at the configured step, and an always-NaN output that declares it may be.

    It has no inputs and no state, so each step converges after one pass.
    """

    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The output that must be finite.
    Value: ClassVar[str] = "Value"
    #: The output that declares it may hold NaN, and does in every step.
    Undefined: ClassVar[str] = "Undefined"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: NanSourceConfig) -> None:
        """Builds the source and declares its two outputs.

        Args:
            my_simulation_parameters: Simulation parameters of the run.
            config: Identity and the step at which ``Value`` is NaN.
        """
        self.config: NanSourceConfig = config
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.value_output: ComponentOutput = self.add_output(
            self.component_name, self.Value, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, output_description="Value."
        )
        self.undefined_output: ComponentOutput = self.add_output(
            self.component_name,
            self.Undefined,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Undefined in every step.",
            may_be_non_finite=True,
        )

    def i_save_state(self) -> None:
        """Saves the state; the source has none."""

    def i_restore_state(self) -> None:
        """Restores the state; the source has none."""

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation; nothing to prepare."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Checks nothing."""

    def write_to_report(self) -> List[str]:
        """Returns no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Publishes ``100 + timestep``, or NaN at the configured step, and NaN on the undefined output."""
        value = math.nan if timestep == self.config.nan_step else 100.0 + timestep
        stsv.set_output_value(self.value_output, value)
        stsv.set_output_value(self.undefined_output, math.nan)


def run_one_day(tmp_path: Path, nan_step: Optional[int]) -> pd.DataFrame:
    """Runs one hourly day with the stub source, NaN at ``nan_step`` when given, and returns the result table.

    Args:
        tmp_path: Directory for the simulator's module directory and results.
        nan_step: The step at which the source publishes NaN, or None for a clean run.

    Returns:
        The result table of the run, one column per output.
    """
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    parameters.result_directory = str(tmp_path / "results")
    simulator = Simulator(
        module_directory=str(tmp_path), module_filename="finite_results", my_simulation_parameters=parameters
    )
    simulator.set_simulation_parameters(parameters)
    source = NanSource(parameters, NanSourceConfig(component_id=ComponentID(name="Source"), nan_step=nan_step))
    simulator.add_component(source)
    simulator.run_all_timesteps()
    results: pd.DataFrame = simulator.results_data_frame
    return results


class TestTheRunFailsAfterTheLoopOnANonFiniteColumn:
    """A NaN in a column that does not allow it fails the run once, after the loop; a declared one does not."""

    def test_a_nan_at_a_known_step_fails_the_run_naming_the_column_and_the_step(self, tmp_path: Path) -> None:
        """Catches a check that misses the column, names the wrong step or counts the bad steps wrongly."""
        with pytest.raises(NonFiniteResultsError) as raised:
            run_one_day(tmp_path, nan_step=5)

        message = str(raised.value)
        assert "in 1 columns" in message
        assert "Source # Value [°C]: NaN or infinite from step 5 (2021-01-01 05:00:00), in 1 of 24 steps" in message
        assert "Undefined" not in message

    def test_a_clean_run_with_a_declared_nan_output_passes(self, tmp_path: Path) -> None:
        """Catches a check that fails a clean run or one that ignores the declaration of an always-NaN output."""
        results = run_one_day(tmp_path, nan_step=None)

        assert results.iloc[:, 1].isna().all()
        assert np.isfinite(results.iloc[:, 0].to_numpy()).all()


class TestTheColumnsAreFoundOverTheWholeTable:
    """The check finds infinities as well as NaN and clears a finite column whose sum overflows."""

    @staticmethod
    def outputs(count: int) -> List[ComponentOutput]:
        """Returns ``count`` hand-built outputs of one component, named ``Column0``, ``Column1`` and so on."""
        return [
            ComponentOutput(
                "Table", f"Column{index}", lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID(name="Table")
            )
            for index in range(count)
        ]

    def test_infinities_are_found_with_their_first_step_and_count(self) -> None:
        """Catches a check that only looks for NaN, or one that reports the last bad step instead of the first."""
        index = pd.date_range("2021-01-01", periods=4, freq="900s")
        results = pd.DataFrame({"a": [1.0, math.inf, 3.0, -math.inf], "b": [1.0, 2.0, 3.0, 4.0]}, index=index)

        found = FiniteResultsCheck.non_finite_columns(results, self.outputs(2))

        assert [(column.output.field_name, column.first_step, column.steps) for column in found] == [("Column0", 1, 2)]
        assert found[0].first_time == "2021-01-01 00:15:00"

    def test_a_finite_column_whose_sum_overflows_passes(self) -> None:
        """Catches a check that takes a column's overflowing sum for a non-finite value."""
        results = pd.DataFrame({"a": [1e308, 1e308, 1e308]}, index=pd.date_range("2021-01-01", periods=3, freq="h"))

        FiniteResultsCheck.require_finite(results, self.outputs(1))
