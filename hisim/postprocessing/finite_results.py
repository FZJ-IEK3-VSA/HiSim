"""The check, after the time loop, that every result column of a run holds only finite values.

The components of a run do not check on every pass that the values they compute are finite: a NaN or an infinity
passes through their arithmetic into the outputs that publish it. This module checks the result table once, after the
last time step, and fails the run with one error that names every column holding a NaN or an infinity, with its first
such step and the number of such steps. For example, a tank whose step mean turned NaN at step 1234 fails the run
with "DHWStorage # WaterMeanTemperature [°C]: NaN or infinite from step 1234 (2021-01-13 20:30:00), in 3 of 35040
steps".

Why after the run: a check on every call of the hydronic arithmetic cost about a tenth of a run's time, and the
column, the step and the count are enough to start looking for the cause.

An output that may hold NaN or infinite values says so where it is declared, with
``add_output(..., may_be_non_finite=True)``; its column is not checked. Every other column is.
"""

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np
import pandas as pd

from hisim.component import ComponentOutput


class NonFiniteResultsError(ValueError):

    """A run whose result table holds NaN or infinite values in a column that does not allow them.

    The message names every such column with its component, output and unit, its first bad step (index and time)
    and the number of bad steps. It is a ``ValueError``, as the energy balance's error is.
    """


@dataclass(frozen=True)
class NonFiniteColumn:

    """One result column that holds NaN or infinite values: its output, its first bad step and the bad steps' count.

    For example, the column of ``DHWStorage # WaterMeanTemperature`` with ``first_step=1234`` and ``steps=3`` is NaN
    or infinite in three steps, the first of them step 1234.
    """

    #: The output whose column it is.
    output: ComponentOutput
    #: The index of the first step whose value is NaN or infinite.
    first_step: int
    #: The time of that step, as the result table's index names it.
    first_time: str
    #: The number of steps whose value is NaN or infinite.
    steps: int

    def describe(self, total_steps: int) -> str:
        """Return one line that names the column, its first bad step and the count of bad steps.

        For example, "DHWStorage # WaterMeanTemperature [°C]: NaN or infinite from step 1234 (2021-01-13 20:30:00),
        in 3 of 35040 steps".

        Args:
            total_steps: The number of steps of the run.

        Returns:
            The line, without a trailing newline.
        """
        return (
            f"{self.output.component_name} # {self.output.field_name} [{self.output.unit.value}]: NaN or infinite "
            f"from step {self.first_step} ({self.first_time}), in {self.steps} of {total_steps} steps"
        )


class FiniteResultsCheck:

    """Finds the result columns that hold NaN or infinite values and fails the run on them.

    A namespace of two functions over the result table, whose columns are the run's outputs in order. The table is
    read with vectorized operations, one pass over its values plus one per suspect column, never step by step. For
    example, ``FiniteResultsCheck.require_finite(results, all_outputs)`` returns nothing for a clean run and raises
    :class:`NonFiniteResultsError` otherwise.
    """

    @staticmethod
    def non_finite_columns(results: pd.DataFrame, all_outputs: Sequence[ComponentOutput]) -> List[NonFiniteColumn]:
        """Return every column that holds a NaN or an infinity, in the order of the outputs.

        A column whose output declares ``may_be_non_finite`` is skipped. The columns are found in two stages: the sum
        of a column is NaN or infinite whenever one of its values is, so one vectorized sum over the table finds the
        suspects; each suspect column is then read value by value with ``np.isfinite``, which also clears a column
        whose finite values only sum beyond the float range. Neither stage copies the table when all its columns are
        floats, which is how the simulator builds it.

        Args:
            results: The result table, one column per output and one row per step.
            all_outputs: The run's outputs, in the order of the table's columns.

        Returns:
            The columns that hold NaN or infinite values, each with its first bad step and the count of bad steps.

        Raises:
            ValueError: If the table does not have one column per output.
        """
        if results.shape[1] != len(all_outputs):
            raise ValueError(f"The result table has {results.shape[1]} columns for {len(all_outputs)} outputs.")
        values = results.to_numpy(dtype=float)
        with np.errstate(over="ignore", invalid="ignore"):
            suspects = np.flatnonzero(~np.isfinite(values.sum(axis=0)))
        found: List[NonFiniteColumn] = []
        for column in suspects.tolist():
            output = all_outputs[column]
            if output.may_be_non_finite:
                continue
            bad = ~np.isfinite(values[:, column])
            if not bad.any():
                continue
            first_step = int(np.argmax(bad))
            first_time = str(results.index[first_step])
            steps = int(bad.sum())
            found.append(NonFiniteColumn(output=output, first_step=first_step, first_time=first_time, steps=steps))
        return found

    @classmethod
    def require_finite(cls, results: pd.DataFrame, all_outputs: Sequence[ComponentOutput]) -> None:
        """Fail the run when a result column that does not allow it holds a NaN or an infinity.

        For example, a clean run passes; a run whose tank published NaN at step 1234 fails with one line for that
        column.

        Args:
            results: The result table, one column per output and one row per step.
            all_outputs: The run's outputs, in the order of the table's columns.

        Raises:
            NonFiniteResultsError: If a column holds NaN or infinite values; the message names every such column.
        """
        found = cls.non_finite_columns(results, all_outputs)
        if not found:
            return
        lines = "\n".join(f"- {column.describe(len(results))}" for column in found)
        raise NonFiniteResultsError(
            f"The results of this run hold NaN or infinite values in {len(found)} columns:\n{lines}\n"
            "An output that may hold such values declares it with add_output(..., may_be_non_finite=True)."
        )
