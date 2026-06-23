"""Unit tests for the charts module."""

from __future__ import annotations

import tempfile

import pandas as pd
import pytest

from hisim.postprocessing.charts import Carpet
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
