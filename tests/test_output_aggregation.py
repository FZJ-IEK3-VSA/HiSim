"""Tests for the aggregation every component output declares, and for which outputs get charts.

An output declares how its time steps are combined into monthly values and a run total
(:class:`hisim.loadtypes.OutputAggregation`); one that declares nothing follows its unit, as all
outputs did before. Outputs declared ``NONE`` and outputs constant over the run get no charts,
and the report lists the constant ones once (:class:`hisim.postprocessing.chart_selection.ChartSelection`).
The resampling itself is tested in ``test_simulator_get_std_results.py``, the bar chart's axis
label in ``test_charts.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List

import numpy as np
import pandas as pd
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.config import ComponentID
from hisim.postprocessing import charts
from hisim.postprocessing.chart_selection import ChartSelection, ConstantOutput
from hisim.postprocessing.postprocessing_main import PostProcessor

pytestmark: pytest.MarkDecorator = pytest.mark.base


def _output(field_name: str, unit: lt.Units, aggregation: lt.OutputAggregation | None = None) -> cp.ComponentOutput:
    """Build one output of a component named ``Comp``."""
    return cp.ComponentOutput(
        object_name="Comp",
        field_name=field_name,
        load_type=lt.LoadTypes.ANY,
        unit=unit,
        component_id=ComponentID("Comp"),
        aggregation=aggregation,
    )


def test_the_unit_rule_is_the_rule_of_the_mean_units() -> None:
    """Every unit in UNITS_USING_MEAN_AGGREGATION is averaged and every other one summed, as before."""
    for unit in lt.Units:
        expected = lt.OutputAggregation.MEAN if unit in lt.UNITS_USING_MEAN_AGGREGATION else lt.OutputAggregation.SUM
        assert lt.OutputAggregation.of_unit(unit) == expected
        assert lt.OutputAggregation.of_unit(unit.value) == expected


def test_an_output_that_declares_nothing_follows_its_unit() -> None:
    """A power is averaged and an energy summed when the output declares no aggregation."""
    assert _output("Power", lt.Units.WATT).aggregation == lt.OutputAggregation.MEAN
    assert _output("Energy", lt.Units.WATT_HOUR).aggregation == lt.OutputAggregation.SUM


def test_a_declared_aggregation_overrides_the_unit() -> None:
    """A running total in Wh declared LAST stays LAST, although its unit would be summed."""
    assert _output("Total", lt.Units.WATT_HOUR, lt.OutputAggregation.LAST).aggregation == lt.OutputAggregation.LAST


def _selection_case() -> tuple[List[cp.ComponentOutput], pd.DataFrame]:
    """Four outputs over 96 time steps: a changing power, a constant, a changing mode declared NONE, a NaN column."""
    outputs = [
        _output("Power", lt.Units.WATT),
        _output("MinimumPower", lt.Units.WATT),
        _output("Mode", lt.Units.ANY, lt.OutputAggregation.NONE),
        _output("Missing", lt.Units.WATT),
    ]
    steps = np.arange(96, dtype=float)
    results = pd.DataFrame(
        {"p": steps, "c": np.full(96, -10000.0), "m": (steps % 3) - 1.0, "n": np.full(96, np.nan)},
        index=pd.date_range("2021-01-01", periods=96, freq="900s"),
    )
    return outputs, results


def test_constant_outputs_and_outputs_without_aggregate_are_not_charted() -> None:
    """Only the changing outputs with an aggregate are charted; the constant one is kept with its value."""
    outputs, results = _selection_case()

    selection = ChartSelection.of_results(outputs, results)

    assert [(index, output.field_name) for index, output in selection.charted_outputs] == [(0, "Power"), (3, "Missing")]
    assert selection.constant_outputs == [ConstantOutput("Comp", "MinimumPower", -10000.0, "W")]
    assert [output.field_name for output in selection.outputs_without_aggregate] == ["Mode"]


def test_a_constant_output_declared_none_is_listed_as_constant() -> None:
    """A mode that never changes is reported with its value rather than dropped silently."""
    outputs = [_output("Mode", lt.Units.ANY, lt.OutputAggregation.NONE)]
    results = pd.DataFrame({"m": np.zeros(10)})

    selection = ChartSelection.of_results(outputs, results)

    assert selection.constant_outputs == [ConstantOutput("Comp", "Mode", 0.0, "-")]
    assert not selection.outputs_without_aggregate


def test_the_constant_outputs_table_has_a_header_and_one_row_per_output() -> None:
    """The report's table names component, output, value and unit."""
    outputs, results = _selection_case()

    table = ChartSelection.of_results(outputs, results).constant_outputs_table()

    assert table == [["Component", "Output", "Value", "Unit"], ["Comp", "MinimumPower", "-10000", "W"]]


def test_a_negative_zero_is_reported_as_zero() -> None:
    """A storage publishing -0.0 all year is listed with the value 0, not -0."""
    assert ConstantOutput("Storage", "ThermalPowerFromSecondaryHeatGenerator", -0.0, "W").report_row()[2] == "0"


class _RecordingChart:
    """Stands in for a chart class and records the outputs it was asked to draw."""

    drawn: List[str] = []

    def __init__(self, output: str, **_kwargs: Any) -> None:
        """Record the output's name."""
        self.output = output

    def plot(self, **_kwargs: Any) -> str:
        """Record the drawing and return a stand-in report entry."""
        _RecordingChart.drawn.append(self.output)
        return self.output


def test_the_line_and_bar_charts_draw_only_the_charted_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The constant output and the output declared NONE get neither a line nor a bar chart."""
    outputs, results = _selection_case()
    selection = ChartSelection.of_results(outputs, results)
    ppdt = SimpleNamespace(
        all_outputs=outputs,
        results=results,
        results_monthly=results.resample("ME").mean(),
        simulation_parameters=SimpleNamespace(result_directory="unused", figure_format="png"),
        time_correction_factor_in_hours_per_timestep=0.25,
    )
    for output in outputs:
        output.output_description = "description"
    monkeypatch.setattr(charts, "Line", _RecordingChart)
    monkeypatch.setattr(charts, "BarChart", _RecordingChart)
    _RecordingChart.drawn = []
    entries: List[Any] = []

    PostProcessor().make_line_plots(ppdt, selection, report_image_entries=entries)  # type: ignore[arg-type]
    PostProcessor().make_monthly_bar_charts(ppdt, selection, report_image_entries=entries)  # type: ignore[arg-type]

    assert _RecordingChart.drawn == ["Comp # Power", "Comp # Missing"] * 2
    assert entries == _RecordingChart.drawn


class _RecordingReport:
    """Stands in for the report generator and records what is written into it."""

    def __init__(self) -> None:
        """Start with an empty record."""
        self.headings: List[Any] = []
        self.tables: List[Any] = []
        self.texts: List[Any] = []

    def write_heading_with_style_heading_one(self, lines: List[str]) -> None:
        """Record a chapter heading."""
        self.headings.append(lines)

    def write_tables_to_report(self, table: List[List[str]]) -> None:
        """Record a table."""
        self.tables.append(table)

    def write_with_normal_alignment(self, lines: List[str]) -> None:
        """Record a paragraph."""
        self.texts.append(lines)

    def page_break(self) -> None:
        """Ignore the page break."""


def test_the_report_lists_the_constant_outputs_once() -> None:
    """One chapter names every constant output with its value and unit, and says why it has no charts."""
    outputs, results = _selection_case()
    report = _RecordingReport()
    processor = PostProcessor()
    processor.chapter_counter = 7
    selection = ChartSelection.of_results(outputs, results)

    processor.write_constant_outputs_to_report(report, selection)  # type: ignore[arg-type]

    assert report.headings == [["7. Constant Outputs"]]
    assert report.tables == [[["Component", "Output", "Value", "Unit"], ["Comp", "MinimumPower", "-10000", "W"]]]
    assert "no charts" in report.texts[0][0]


def test_no_chapter_is_written_when_nothing_is_constant() -> None:
    """A run without constant outputs gets no empty chapter."""
    outputs = [_output("Power", lt.Units.WATT)]
    report = _RecordingReport()

    PostProcessor().write_constant_outputs_to_report(
        report, ChartSelection.of_results(outputs, pd.DataFrame({"p": [1.0, 2.0]}))  # type: ignore[arg-type]
    )

    assert not report.headings and not report.tables
