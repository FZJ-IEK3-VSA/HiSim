"""Unit tests for the single-day chart postprocessing module."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hisim.postprocessing.chart_singleday import ChartSingleDay


pytestmark: pytest.MarkDecorator = pytest.mark.base

LABEL_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _make_series(n_steps: int) -> pd.Series:
    """A plain pandas Series of ``n_steps`` rows with an integer RangeIndex."""
    return pd.Series(np.arange(n_steps, dtype=float))


def test_build_day_slice_is_static_and_needs_no_chart_construction() -> None:
    """``build_day_slice`` must be a pure static helper (no ``self``, no Chart)."""
    assert isinstance(
        ChartSingleDay.__dict__["build_day_slice"],
        staticmethod,
    ), "build_day_slice should be a @staticmethod so it is callable without a ChartSingleDay instance"


def test_build_day_slice_first_day_of_january_with_hourly_resolution() -> None:
    """Day 0 / month 0 -> January 1st, the first 24 timesteps of the series (slice path)."""
    # 1 / time_correction_factor == 1 -> one timestep per hour.
    data = _make_series(24 * 31)  # 31 days of hourly data
    data_slice, plot_title = ChartSingleDay.build_day_slice(
        data=data,
        month=0,
        day=0,
        time_correction_factor=1.0,
        label_months_lowercase=LABEL_MONTHS,
        title="Temperature",
    )
    assert plot_title == "Temperature January 1st"
    assert len(data_slice) == 24
    assert list(data_slice.values) == [float(v) for v in range(24)]


def test_build_day_slice_slice_path_requires_more_than_one_day_of_data() -> None:
    """Boundary: a slice of width 24 is only taken when ``len(data) > 24``.

    With exactly 24 points ``abs(lastindex - firstindex) == 24`` is not ``< 24``,
    so the full series is returned unchanged (this mirrors the original guard).
    """
    data = _make_series(24)
    data_slice, _ = ChartSingleDay.build_day_slice(
        data=data,
        month=0,
        day=0,
        time_correction_factor=1.0,
        label_months_lowercase=LABEL_MONTHS,
        title="T",
    )
    assert data_slice is data
    assert len(data_slice) == 24


def test_build_day_slice_ordinal_suffixes() -> None:
    """The ordinal suffix follows day_number = day + 1: 1st, 2nd, 3rd, then th.

    Uses short data so the bounds check falls through to the full series (the
    slice path only works for ``firstindex == 0``); this isolates the title logic.
    """
    data = _make_series(5)  # len <= 24 -> fall-through, no slicing
    _, t1 = ChartSingleDay.build_day_slice(
        data=data, month=0, day=0, time_correction_factor=1.0,
        label_months_lowercase=LABEL_MONTHS, title="X")
    _, t2 = ChartSingleDay.build_day_slice(
        data=data, month=0, day=1, time_correction_factor=1.0,
        label_months_lowercase=LABEL_MONTHS, title="X")
    _, t3 = ChartSingleDay.build_day_slice(
        data=data, month=0, day=2, time_correction_factor=1.0,
        label_months_lowercase=LABEL_MONTHS, title="X")
    _, t4 = ChartSingleDay.build_day_slice(
        data=data, month=0, day=3, time_correction_factor=1.0,
        label_months_lowercase=LABEL_MONTHS, title="X")
    _, t11 = ChartSingleDay.build_day_slice(
        data=data, month=0, day=10, time_correction_factor=1.0,
        label_months_lowercase=LABEL_MONTHS, title="X")

    assert t1 == "X January 1st"
    assert t2 == "X January 2nd"
    assert t3 == "X January 3rd"
    assert t4 == "X January 4th"
    assert t11 == "X January 11th"


def test_build_day_slice_month_label_and_offset_in_title() -> None:
    """Month index selects the label and the day offset is encoded in the title."""
    data = _make_series(5)  # fall-through path
    _, plot_title = ChartSingleDay.build_day_slice(
        data=data,
        month=1,
        day=2,
        time_correction_factor=1.0,
        label_months_lowercase=LABEL_MONTHS,
        title="Demand",
    )
    # month=1 -> February, day=2 -> day_number 3 -> "3rd"
    assert plot_title == "Demand February 3rd"


def test_build_day_slice_firstindex_offset_for_subhourly_resolution() -> None:
    """15-minute resolution -> 4 timesteps per hour, 96 per day, sliced from offset 0."""
    seconds_per_timestep = 900
    time_correction_factor = seconds_per_timestep / 3600.0  # 0.25
    data = _make_series(96 * 31)
    data_slice, plot_title = ChartSingleDay.build_day_slice(
        data=data,
        month=0,
        day=0,
        time_correction_factor=time_correction_factor,
        label_months_lowercase=LABEL_MONTHS,
        title="PV",
    )
    assert plot_title == "PV January 1st"
    assert len(data_slice) == 96
    assert list(data_slice.values) == [float(v) for v in range(96)]


def test_build_day_slice_returns_full_data_when_slice_exceeds_length() -> None:
    """When the requested day slice would exceed ``len(data)``, the whole series is returned unchanged."""
    # Only 5 timesteps available, but the requested slice needs 24 -> out of bounds.
    short_data = _make_series(5)
    data_slice, plot_title = ChartSingleDay.build_day_slice(
        data=short_data,
        month=0,
        day=0,
        time_correction_factor=1.0,
        label_months_lowercase=LABEL_MONTHS,
        title="Short",
    )
    assert plot_title == "Short January 1st"
    # Boundary: abs(lastindex - firstindex) == 24 is NOT < 5, so fall-through to full data.
    assert data_slice is short_data
    assert len(data_slice) == 5


def test_build_day_slice_does_not_mutate_input_series() -> None:
    """The helper must not mutate the caller's series in place."""
    data = _make_series(24 * 31)
    original_index = list(data.index)
    original_values = list(data.values)
    ChartSingleDay.build_day_slice(
        data=data,
        month=0,
        day=0,
        time_correction_factor=1.0,
        label_months_lowercase=LABEL_MONTHS,
        title="T",
    )
    assert list(data.index) == original_index
    assert list(data.values) == original_values


def test_get_day_data_delegates_to_build_day_slice(tmp_path) -> None:
    """``get_day_data`` sets ``self.plot_title`` and returns the slice from ``build_day_slice``."""
    from hisim.simulationparameters import FigureFormat

    timesteps_per_hour = 1
    data = _make_series(24 * 31)
    chart = ChartSingleDay(
        output="Residence # Temperature",
        component_name="House",
        units="C",
        directory_path=str(tmp_path),
        time_correction_factor=1.0 / timesteps_per_hour,
        output_description="temperature",
        data=data,
        day=0,
        month=0,
        figure_format=FigureFormat.PNG,
        path_checker=lambda **_: None,
    )
    result = chart.get_day_data()
    assert chart.plot_title.endswith("January 1st")
    assert len(result) == 24

    # Consistency with the pure helper.
    expected_slice, expected_title = ChartSingleDay.build_day_slice(
        data=data,
        month=0,
        day=0,
        time_correction_factor=1.0 / timesteps_per_hour,
        label_months_lowercase=chart.label_months_lowercase,
        title=chart.title,
    )
    assert chart.plot_title == expected_title
    assert list(result.values) == list(expected_slice.values)
