"""Unit tests for the charts module."""

from __future__ import annotations

import tempfile

from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
import pytest

from hisim.postprocessing.charts import Carpet
from hisim.postprocessing.chartbase import Chart
from hisim.simulationparameters import FigureFormat


pytestmark: pytest.MarkDecorator = pytest.mark.base


def test_carpet_plot_returns_none_on_invalid_data() -> None:
    """Carpet.plot returns None (does not raise NameError) when data cannot be reshaped into entire days."""
    # Create a Carpet instance with minimal mocks
    with tempfile.TemporaryDirectory() as tmpdir:
        carpet = Carpet(
            output="Test # Output",
            component_name="TestComponent",
            units="kWh",
            directory_path=tmpdir,
            time_correction_factor=3600,
            output_description="Test output description",
            figure_format=FigureFormat.PNG,
        )

        # Pass data with a length that is NOT evenly divisible,
        # so data.values.reshape(xdims, ydims) will raise ValueError
        invalid_data = pd.Series([1.0, 2.0, 3.0])  # length 3, not divisible by any xdims > 1
        xdims = 2  # ydims = 3 / 2 = 1 (non-integer), reshape will fail

        result = carpet.plot(xdims=xdims, data=invalid_data)

        assert result is None, "Carpet.plot should return None on invalid data"


class _ChartFixture(NamedTuple):
    """A Chart built with an injected path checker, plus the captured calls and target directory."""

    chart: Chart
    checker_calls: list[str]
    target_subdir: Path


@pytest.fixture
def chart_with_injected_checker(tmp_path: Path) -> _ChartFixture:
    """Build a Chart whose path-length checks are captured instead of hitting the global singleton.

    Regression scaffolding for the constructor side effects that used to live in
    ``Chart.__init__``: it previously called ``Path(...).mkdir(...)`` unconditionally and used the
    global ``result_path_provider.check_path_length`` singleton, so no chart (nor any subclass)
    could be constructed in a test without touching disk and the global provider. Both are now
    removed from ``__init__``; this fixture constructs the chart with a recording checker and a
    fresh ``tmp_path`` so each focused test can assert one independent behaviour.
    """
    checker_calls: list[str] = []

    def fake_path_checker(path: str) -> None:
        checker_calls.append(path)

    chart = Chart(
        output="SomeComponent # SomeOutput",
        component_name="SomeComponent",
        output_description="description",
        chart_type="Line",
        units="kW",
        directory_path=str(tmp_path),
        time_correction_factor=3600,
        figure_format=FigureFormat.PNG,
        path_checker=fake_path_checker,
    )
    target_subdir = tmp_path / "SomeComponent" / "SomeOutput"
    return _ChartFixture(chart=chart, checker_calls=checker_calls, target_subdir=target_subdir)


def test_chart_constructor_does_not_create_output_directory(  # pylint: disable=redefined-outer-name
    chart_with_injected_checker: _ChartFixture,
) -> None:
    """Chart.__init__ must not create the per-component output directory."""
    assert not chart_with_injected_checker.target_subdir.exists()


def test_chart_constructor_uses_injected_path_checker(  # pylint: disable=redefined-outer-name
    chart_with_injected_checker: _ChartFixture,
) -> None:
    """Chart.__init__ must invoke the injected path_checker (not the global singleton) for both paths."""
    chart = chart_with_injected_checker.chart
    assert chart_with_injected_checker.checker_calls == [chart.filepath, chart.filepath2]


def test_ensure_output_dir_creates_directory_lazily(  # pylint: disable=redefined-outer-name
    chart_with_injected_checker: _ChartFixture,
) -> None:
    """ensure_output_dir creates the per-component directory on demand, not in __init__."""
    chart_with_injected_checker.chart.ensure_output_dir()
    assert chart_with_injected_checker.target_subdir.exists()
    assert chart_with_injected_checker.target_subdir.is_dir()


def test_rescale_y_axis_scales_watts_to_megawatts(  # pylint: disable=redefined-outer-name
    chart_with_injected_checker: _ChartFixture,
) -> None:
    """rescale_y_axis rescales watt-scale values to megawatts without filesystem side effects."""
    y = np.array([0.0, 1500.0, 3_000_000.0])
    scaled, scaled_units = chart_with_injected_checker.chart.rescale_y_axis(
        y_values=y.copy(), units="W"
    )
    assert scaled_units == "MW"
    assert scaled[1] == pytest.approx(1500.0 * 1e-6)


def test_chart_constructor_defaults_to_global_path_checker(tmp_path: Path) -> None:
    """When no path_checker is injected, Chart.__init__ falls back to the global one.

    On non-Windows hosts ``check_path_length`` is a no-op, so this just confirms the
    default wiring still works and construction stays side-effect-free for the mkdir.
    """
    target_subdir = tmp_path / "Comp" / "Out"
    chart = Chart(
        output="Comp # Out",
        component_name="Comp",
        output_description="description",
        chart_type="Bar",
        units="kWh",
        directory_path=str(tmp_path),
        time_correction_factor=3600,
        figure_format=FigureFormat.PNG,
    )

    # No directory created by the constructor even with the default checker.
    assert not target_subdir.exists()
    chart.ensure_output_dir()
    assert target_subdir.exists()
