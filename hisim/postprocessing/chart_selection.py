"""Which outputs of a run get charts, and which constant outputs the report lists instead.

A chart of an output is worth drawing only when the output changes and its values can be read
over time. Two kinds of output get no line, carpet, single-day or monthly bar chart:

- an output with the same value in every time step of the run (a constant parameter such as
  ``MinimumChargingPowerInWatt``, or a cooling output that is 0 in a house that is never cooled):
  its charts would be flat lines and equal bars. The report lists it once, with its value and unit;
- an output that declares the aggregation ``NONE`` (:class:`~hisim.loadtypes.OutputAggregation`):
  a controller mode, a timer that restarts at every switch, an angle of the sun. No monthly value
  of it means anything, and by the owner's decision of 2026-10-15 it gets no charts at all.

The CSV and pickle exports are not affected: they always contain every output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar, List, Tuple

import pandas as pd

from hisim.component import ComponentOutput
from hisim.loadtypes import OutputAggregation, Units


@dataclass(frozen=True)
class ConstantOutput:
    """An output that has the same value in every time step of the run.

    Example: ``ConstantOutput("Battery", "MinimumChargingPowerInWatt", -10000.0, "W")``.
    """

    component_name: str
    field_name: str
    value: float
    unit: str

    def report_row(self) -> List[str]:
        """Return the output's row in the report's table of constant outputs.

        The value is written with six significant digits, e.g. ``-10000`` or ``0.123457``. A
        negative zero, which a component computing ``-1 * 0.0`` publishes, is written as ``0``.

        Returns:
            The component, the output, the value and the unit, as text.
        """
        value_without_negative_zero = self.value + 0.0
        return [self.component_name, self.field_name, f"{value_without_negative_zero:.6g}", self.unit]


class ChartSelection:
    """The outputs of one run that get charts, and the constant outputs the report lists instead.

    Built once per run from the result table (:meth:`of_results`); the line, carpet, single-day
    and monthly bar charts all draw :attr:`charted_outputs` and nothing else.
    """

    #: The header row of the report's table of constant outputs.
    REPORT_TABLE_HEADER: ClassVar[Tuple[str, ...]] = ("Component", "Output", "Value", "Unit")

    def __init__(
        self,
        charted_outputs: List[Tuple[int, ComponentOutput]],
        constant_outputs: List[ConstantOutput],
        outputs_without_aggregate: List[ComponentOutput],
    ) -> None:
        """Hold the selection.

        Args:
            charted_outputs: The outputs that get charts, each with its column index in the result table.
            constant_outputs: The outputs with one value over the whole run.
            outputs_without_aggregate: The changing outputs that declare the aggregation ``NONE``.
        """
        self.charted_outputs: List[Tuple[int, ComponentOutput]] = charted_outputs
        self.constant_outputs: List[ConstantOutput] = constant_outputs
        self.outputs_without_aggregate: List[ComponentOutput] = outputs_without_aggregate

    @classmethod
    def of_results(cls, all_outputs: List[ComponentOutput], results: pd.DataFrame) -> "ChartSelection":
        """Sort the outputs of a run into charted, constant and without aggregate.

        An output is constant when its smallest and largest value over the run are equal. A
        column that is NaN in every time step has neither and is charted as before. A constant
        output is listed as constant whatever aggregation it declares.

        Args:
            all_outputs: The run's outputs, in the order of the result table's columns.
            results: The result table, one column per output and one row per time step.

        Returns:
            The selection.
        """
        charted_outputs: List[Tuple[int, ComponentOutput]] = []
        constant_outputs: List[ConstantOutput] = []
        outputs_without_aggregate: List[ComponentOutput] = []
        for index, output in enumerate(all_outputs):
            column = results.iloc[:, index]
            minimum, maximum = column.min(), column.max()
            if not math.isnan(minimum) and minimum == maximum:
                unit = output.unit.value if isinstance(output.unit, Units) else str(output.unit)
                constant_outputs.append(ConstantOutput(output.component_name, output.field_name, float(minimum), unit))
            elif output.aggregation == OutputAggregation.NONE:
                outputs_without_aggregate.append(output)
            else:
                charted_outputs.append((index, output))
        return cls(charted_outputs, constant_outputs, outputs_without_aggregate)

    def constant_outputs_table(self) -> List[List[str]]:
        """Return the report's table of constant outputs, header row first.

        Returns:
            One row per constant output, in the order of the result table.
        """
        return [list(self.REPORT_TABLE_HEADER)] + [constant.report_row() for constant in self.constant_outputs]
