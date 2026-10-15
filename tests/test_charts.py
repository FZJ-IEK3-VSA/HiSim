"""Tests for Chart and Carpet postprocessing chart helpers.

Covers Carpet.plot behaviour on invalid input data, Chart constructor path-checking
and lazy output-directory creation, and rescale_y_axis unit conversion.
"""

from __future__ import annotations

import datetime
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
            time_correction_factor_in_hours=3600,
            output_description="Test output description",
            figure_format=FigureFormat.PNG,
        )

        # Pass data with a length that is NOT evenly divisible,
        # so data.values.reshape(xdims, ydims) will raise ValueError
        invalid_data = pd.Series([1.0, 2.0, 3.0])  # length 3, not divisible by any xdims > 1
        xdims = 2  # ydims = 3 / 2 = 1 (non-integer), reshape will fail

        result = carpet.plot(
            xdims=xdims, data_in_self_units=invalid_data, window_start=datetime.datetime(2021, 1, 1)
        )

        assert result is None, "Carpet.plot should return None on invalid data"


@pytest.mark.parametrize(
    ("window_start", "number_of_days", "expected_first", "expected_last", "expected_title"),
    [
        # A full year from 1 January: twelve months, each in the column of its 15th.
        (datetime.datetime(2021, 1, 1), 365, (14.5, "JAN"), (348.5, "DEC"), "Month of the year"),
        # A leap year has one more day before March, and every month is still found.
        (datetime.datetime(2020, 1, 1), 366, (14.5, "JAN"), (349.5, "DEC"), "Month of the year"),
        # Three summer months: the labels follow the window's own dates, not January's.
        (datetime.datetime(2021, 6, 1), 92, (14.5, "JUN"), (75.5, "AUG"), "Month of the year"),
        # A week in July is labelled by its dates.
        (datetime.datetime(2021, 7, 1), 7, (0.5, "01 Jul"), (6.5, "07 Jul"), "Day"),
    ],
)
def test_carpet_x_axis_is_labelled_from_the_window_dates(
    window_start: datetime.datetime,
    number_of_days: int,
    expected_first: tuple,
    expected_last: tuple,
    expected_title: str,
) -> None:
    """The carpet's x axis names the run's own months or days, wherever in the year the run starts."""
    positions, labels, title = Carpet.x_axis_ticks(window_start, number_of_days)
    assert (positions[0], labels[0]) == expected_first
    assert (positions[-1], labels[-1]) == expected_last
    assert title == expected_title


def test_carpet_x_axis_of_a_long_short_window_keeps_at_most_seven_day_ticks() -> None:
    """Twenty days get a tick every third day, so the labels stay readable."""
    positions, labels, _ = Carpet.x_axis_ticks(datetime.datetime(2021, 7, 1), 20)
    assert len(positions) <= Carpet.MAXIMUM_DAY_TICKS
    assert labels[:2] == ["01 Jul", "04 Jul"]


@pytest.mark.parametrize("timesteps_per_day", [1440, 96, 24])
def test_carpet_y_ticks_of_steps_up_to_an_hour_are_the_whole_hours_they_always_were(timesteps_per_day: int) -> None:
    """Up to an hour per step, the ticks sit every six hours of rows, 24 at the bottom, as before."""
    rows_per_hour = timesteps_per_day // 24
    positions, labels = Carpet.y_axis_ticks(timesteps_per_day)
    assert positions == [float(hour * rows_per_hour) for hour in (0, 6, 12, 18, 24)]
    assert labels == ["24", "18", "12", "6", "0"]


def test_carpet_y_ticks_of_four_hour_steps_lie_at_their_hours() -> None:
    """Six rows a day: 18:00 and 06:00 fall inside the rows that hold them."""
    assert Carpet.y_axis_ticks(6)[0] == [0.0, 1.5, 3.0, 4.5, 6.0]


@pytest.mark.parametrize("hours_per_step", [2, 3, 4, 6, 8, 12, 24])
def test_a_carpet_is_drawn_for_every_step_above_an_hour_that_divides_a_day(tmp_path: Path, hours_per_step: int) -> None:
    """A week at two to 24 hours per step gives a carpet of 12 down to one row a day.

    The rows per hour were 0 above an hour, and ``np.arange(0, 0, 0)`` raised ZeroDivisionError.
    """
    carpet = Carpet(
        output="Test # Output",
        component_name="TestComponent",
        units="W",
        directory_path=str(tmp_path),
        time_correction_factor_in_hours=float(hours_per_step),
        output_description="Test output description",
        figure_format=FigureFormat.PNG,
        path_checker=lambda **_: None,
    )
    timesteps_per_day = 24 // hours_per_step
    data = pd.Series(np.arange(7 * timesteps_per_day, dtype=float))

    entry = carpet.plot(xdims=7, data_in_self_units=data, window_start=datetime.datetime(2021, 7, 1))

    assert entry is not None
    assert Path(entry.file_path).is_file()


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
        time_correction_factor_in_hours=3600,
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
        time_correction_factor_in_hours=3600,
        figure_format=FigureFormat.PNG,
    )

    # No directory created by the constructor even with the default checker.
    assert not target_subdir.exists()
    chart.ensure_output_dir()
    assert target_subdir.exists()
