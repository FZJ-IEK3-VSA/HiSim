"""Which outputs of a run get charts, and why the others do not.

A chart of an output is worth drawing only when the output changes and its values can be read
over time. Two kinds of output get no line, carpet, single-day or monthly bar chart:

- an output with the same value in every time step of the run (a constant parameter such as
  ``MinimumChargingPowerInWatt``, or a cooling output that is 0 in a house that is never cooled):
  its charts would be flat lines and equal bars;
- an output that declares the aggregation ``NONE`` (:class:`~hisim.loadtypes.OutputAggregation`):
  a controller mode, a timer that restarts at every switch, an angle of the sun. No monthly value
  of it means anything, and by the owner's decision of 2026-10-15 it gets no charts at all.

The report lists every such output once, with the reason, so no output disappears from the report
silently. The CSV and pickle exports are not affected: they always contain every output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar, List, Optional, Tuple

import pandas as pd

from hisim.component import ComponentOutput
from hisim.loadtypes import OutputAggregation, Units


@dataclass(frozen=True)
class OutputWithoutCharts:
    """An output of the run that gets no charts, and the value it kept if it was constant.

    Examples: ``OutputWithoutCharts("Battery", "MinimumChargingPowerInWatt", "W", -10000.0)`` is
    constant; ``OutputWithoutCharts("Weather", "Azimuth", "°", None)`` changes but declares no
    aggregate.
    """

    component_name: str
    field_name: str
    unit: str
    #: The value in every time step if the output is constant, else ``None``.
    constant_value: Optional[float]

    @property
    def is_constant(self) -> bool:
        """Return whether the output had one value over the whole run."""
        return self.constant_value is not None

    def reason(self) -> str:
        """Return why the output has no charts, as the report states it.

        A constant value is written with six significant digits, e.g. ``constant at -10000`` or
        ``constant at 0.123457``. A negative zero, which a component computing ``-1 * 0.0``
        publishes, is written as ``0``.

        Returns:
            ``"constant at <value>"`` or ``"no aggregate declared"``.
        """
        if self.constant_value is None:
            return "no aggregate declared"
        value_without_negative_zero = self.constant_value + 0.0
        return f"constant at {value_without_negative_zero:.6g}"

    def report_row(self) -> List[str]:
        """Return the output's row in the report's table of outputs without charts.

        Returns:
            The component, the output, the unit and the reason, as text.
        """
        return [self.component_name, self.field_name, self.unit, self.reason()]


class ChartSelection:
    """The outputs of one run that get charts, and those the report lists instead.

    Built once per run from the result table (:meth:`of_results`); the line, carpet, single-day
    and monthly bar charts all draw :attr:`charted_outputs` and nothing else.
    """

    #: The header row of the report's table of outputs without charts.
    REPORT_TABLE_HEADER: ClassVar[Tuple[str, ...]] = ("Component", "Output", "Unit", "Reason")

    def __init__(
        self,
        charted_outputs: List[Tuple[int, ComponentOutput]],
        outputs_without_charts: List[OutputWithoutCharts],
    ) -> None:
        """Hold the selection.

        Args:
            charted_outputs: The outputs that get charts, each with its column index in the result table.
            outputs_without_charts: The other outputs, in the order of the result table.
        """
        self.charted_outputs: List[Tuple[int, ComponentOutput]] = charted_outputs
        self.outputs_without_charts: List[OutputWithoutCharts] = outputs_without_charts

    @property
    def constant_outputs(self) -> List[OutputWithoutCharts]:
        """Return the outputs without charts that had one value over the whole run."""
        return [output for output in self.outputs_without_charts if output.is_constant]

    @classmethod
    def of_results(cls, all_outputs: List[ComponentOutput], results: pd.DataFrame) -> "ChartSelection":
        """Sort the outputs of a run into those that get charts and those that do not.

        An output is constant when its smallest and largest value over the run are equal. A
        column that is NaN in every time step has neither and is charted as before. A constant
        output is reported as constant whatever aggregation it declares, since its value says more
        than its declaration.

        Args:
            all_outputs: The run's outputs, in the order of the result table's columns.
            results: The result table, one column per output and one row per time step.

        Returns:
            The selection.
        """
        charted_outputs: List[Tuple[int, ComponentOutput]] = []
        outputs_without_charts: List[OutputWithoutCharts] = []
        for index, output in enumerate(all_outputs):
            column = results.iloc[:, index]
            minimum, maximum = column.min(), column.max()
            is_constant = not math.isnan(minimum) and minimum == maximum
            if is_constant or output.aggregation == OutputAggregation.NONE:
                unit = output.unit.value if isinstance(output.unit, Units) else str(output.unit)
                constant_value = float(minimum) if is_constant else None
                outputs_without_charts.append(
                    OutputWithoutCharts(output.component_name, output.field_name, unit, constant_value)
                )
            else:
                charted_outputs.append((index, output))
        return cls(charted_outputs, outputs_without_charts)

    def outputs_without_charts_table(self) -> List[List[str]]:
        """Return the report's table of outputs without charts, header row first.

        Returns:
            One row per output without charts, in the order of the result table.
        """
        return [list(self.REPORT_TABLE_HEADER)] + [output.report_row() for output in self.outputs_without_charts]
