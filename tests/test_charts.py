"""Unit tests for the charts module."""

from __future__ import annotations

import tempfile

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


def test_chart_constructor_creates_no_directory_and_uses_injected_path_checker(tmp_path) -> None:
    """Chart.__init__ must not touch the filesystem and must accept an injected path checker.

    Regression test for the side effects that used to live in the constructor:
    ``Chart.__init__`` previously called ``Path(...).mkdir(...)`` unconditionally and
    used the global ``result_path_provider.check_path_length`` singleton, so no chart
    (nor any subclass) could be constructed in a test without touching disk and the
    global provider. Both are now removed from ``__init__``.
    """
    checker_calls: list[str] = []

    def fake_path_checker(path: str) -> None:
        checker_calls.append(path)

    target_subdir = tmp_path / "SomeComponent" / "SomeOutput"
    assert not target_subdir.exists()

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

    # The constructor must NOT have created the per-component output directory.
    assert not target_subdir.exists()

    # The injected checker was used for both generated paths (not the global singleton).
    assert checker_calls == [chart.filepath, chart.filepath2]

    # ensure_output_dir creates the directory lazily, on demand.
    chart.ensure_output_dir()
    assert target_subdir.exists()
    assert target_subdir.is_dir()

    # rescale_y_axis is now reachable without any filesystem/global-provider side effects.
    y = np.array([0.0, 1500.0, 3_000_000.0])
    scaled, scaled_units = chart.rescale_y_axis(y_values=y.copy(), units="W")
    assert scaled_units == "MW"
    assert scaled[1] == pytest.approx(1500.0 * 1e-6)


def test_chart_constructor_defaults_to_global_path_checker(tmp_path) -> None:
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
