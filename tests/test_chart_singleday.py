"""Unit tests for the single-day chart postprocessing module.

The chart shows one day of a run, picked by its position in the run and named by its calendar date,
so a run that starts in July shows and names a July day.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hisim.postprocessing.chart_singleday import ChartSingleDay


pytestmark: pytest.MarkDecorator = pytest.mark.base

LABEL_MONTHS: list[str] = [
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


JANUARY_FIRST: datetime.datetime = datetime.datetime(2021, 1, 1)
JULY_FIRST: datetime.datetime = datetime.datetime(2021, 7, 1)


def _make_series(n_steps: int) -> pd.Series[float]:
    """A plain pandas Series of ``n_steps`` rows with an integer RangeIndex."""
    return pd.Series(np.arange(n_steps, dtype=float))


def test_build_day_slice_is_static_and_needs_no_chart_construction() -> None:
    """``build_day_slice`` must be a pure static helper (no ``self``, no Chart)."""
    assert isinstance(
        ChartSingleDay.__dict__["build_day_slice"],
        staticmethod,
    ), "build_day_slice should be a @staticmethod so it is callable without a ChartSingleDay instance"


def test_build_day_slice_first_day_of_january_with_hourly_resolution() -> None:
    """Day 0 of a run from 1 January -> January 1st, the first 24 timesteps of the series."""
    data = _make_series(24 * 31)  # 31 days of hourly data
    data_slice, plot_title = ChartSingleDay.build_day_slice(
        data_with_units=data,
        window_start=JANUARY_FIRST,
        day_in_window=0,
        timesteps_per_day=24,
        label_months_lowercase=LABEL_MONTHS,
        title="Temperature",
    )
    assert plot_title == "Temperature January 1st"
    assert list(data_slice.values) == [float(v) for v in range(24)]


def test_build_day_slice_names_the_day_of_a_july_run_by_its_july_date() -> None:
    """Day 0 of a run from 1 July is July 1st, not January 1st: the title follows the run's dates."""
    data = _make_series(24 * 7)
    _, plot_title = ChartSingleDay.build_day_slice(
        data_with_units=data,
        window_start=JULY_FIRST,
        day_in_window=0,
        timesteps_per_day=24,
        label_months_lowercase=LABEL_MONTHS,
        title="PV",
    )
    assert plot_title == "PV July 1st"


def test_build_day_slice_takes_a_later_day_with_its_rows_and_its_index() -> None:
    """Day 2 of an hourly run is rows 48 to 71, keeping their own index labels."""
    index = pd.date_range(JULY_FIRST, periods=24 * 7, freq="h")
    data = pd.Series(np.arange(24 * 7, dtype=float), index=index)
    data_slice, plot_title = ChartSingleDay.build_day_slice(
        data_with_units=data,
        window_start=JULY_FIRST,
        day_in_window=2,
        timesteps_per_day=24,
        label_months_lowercase=LABEL_MONTHS,
        title="PV",
    )
    assert plot_title == "PV July 3rd"
    assert list(data_slice.values) == [float(v) for v in range(48, 72)]
    assert data_slice.index[0] == pd.Timestamp(2021, 7, 3)


def test_build_day_slice_ordinal_suffixes() -> None:
    """The ordinal suffix follows the day of the month: 1st, 2nd, 3rd, 4th, 11th, 21st, 22nd, 23rd, 31st."""
    data = _make_series(24 * 31)
    expected = {0: "1st", 1: "2nd", 2: "3rd", 3: "4th", 10: "11th", 11: "12th", 12: "13th",
                20: "21st", 21: "22nd", 22: "23rd", 30: "31st"}
    for day_in_window, ordinal in expected.items():
        _, title = ChartSingleDay.build_day_slice(
            data_with_units=data, window_start=JANUARY_FIRST, day_in_window=day_in_window,
            timesteps_per_day=24, label_months_lowercase=LABEL_MONTHS, title="X")
        assert title == f"X January {ordinal}"


def test_build_day_slice_crosses_into_the_next_month() -> None:
    """Day 31 of a run from 1 January is February 1st."""
    data = _make_series(24 * 40)
    _, plot_title = ChartSingleDay.build_day_slice(
        data_with_units=data,
        window_start=JANUARY_FIRST,
        day_in_window=31,
        timesteps_per_day=24,
        label_months_lowercase=LABEL_MONTHS,
        title="Demand",
    )
    assert plot_title == "Demand February 1st"


def test_build_day_slice_subhourly_resolution() -> None:
    """15-minute resolution -> 96 timesteps per day."""
    data = _make_series(96 * 31)
    data_slice, plot_title = ChartSingleDay.build_day_slice(
        data_with_units=data,
        window_start=JANUARY_FIRST,
        day_in_window=1,
        timesteps_per_day=96,
        label_months_lowercase=LABEL_MONTHS,
        title="PV",
    )
    assert plot_title == "PV January 2nd"
    assert list(data_slice.values) == [float(v) for v in range(96, 192)]


def test_build_day_slice_with_steps_longer_than_an_hour() -> None:
    """Two-hour steps -> 12 timesteps per day, so day 1 is rows 12 to 23 (the old per-hour count was 0)."""
    data_slice, plot_title = ChartSingleDay.build_day_slice(
        data_with_units=_make_series(12 * 3),
        window_start=JANUARY_FIRST,
        day_in_window=1,
        timesteps_per_day=12,
        label_months_lowercase=LABEL_MONTHS,
        title="PV",
    )
    assert plot_title == "PV January 2nd"
    assert list(data_slice.values) == [float(v) for v in range(12, 24)]


def test_build_day_slice_refuses_a_day_the_data_does_not_reach() -> None:
    """A day past the end of the data is refused, instead of plotting the whole series under its date."""
    with pytest.raises(ValueError, match="2021-01-08"):
        ChartSingleDay.build_day_slice(
            data_with_units=_make_series(24 * 7),
            window_start=JANUARY_FIRST,
            day_in_window=7,
            timesteps_per_day=24,
            label_months_lowercase=LABEL_MONTHS,
            title="Short",
        )


def test_build_day_slice_refuses_a_negative_day() -> None:
    """Day -1 would be an empty slice titled with the last day of the year before."""
    with pytest.raises(ValueError, match="cannot be -1"):
        ChartSingleDay.build_day_slice(
            data_with_units=_make_series(24 * 7),
            window_start=JANUARY_FIRST,
            day_in_window=-1,
            timesteps_per_day=24,
            label_months_lowercase=LABEL_MONTHS,
            title="Short",
        )


def test_build_day_slice_does_not_mutate_input_series() -> None:
    """The helper must not mutate the caller's series in place."""
    data = _make_series(24 * 31)
    original_index = list(data.index)
    original_values = list(data.values)
    ChartSingleDay.build_day_slice(
        data_with_units=data,
        window_start=JANUARY_FIRST,
        day_in_window=3,
        timesteps_per_day=24,
        label_months_lowercase=LABEL_MONTHS,
        title="T",
    )
    assert list(data.index) == original_index
    assert list(data.values) == original_values


def _chart(tmp_path: Path, data: pd.Series, window_start: datetime.datetime, day_in_window: int) -> ChartSingleDay:
    """A single-day chart over hourly data, writing nowhere until it plots."""
    from hisim.simulationparameters import FigureFormat

    return ChartSingleDay(
        output="Residence # Temperature",
        component_name="House",
        units="C",
        directory_path=str(tmp_path),
        time_correction_factor_in_hours=1.0,
        output_description="temperature",
        data_with_units=data,
        window_start=window_start,
        timesteps_per_day=24,
        day_in_window=day_in_window,
        figure_format=FigureFormat.PNG,
        path_checker=lambda **_: None,
    )


def test_get_day_data_delegates_to_build_day_slice(tmp_path: Path) -> None:
    """``get_day_data`` sets ``self.plot_title`` and returns the slice from ``build_day_slice``.

    Day 31 of a run from 1 January is 1 February, the first day of its month: a ``get_day_data``
    that sliced by the day of the month, or by day 0, would return other rows than these.
    """
    data = _make_series(24 * 40)
    chart = _chart(tmp_path, data, JANUARY_FIRST, 31)
    result = chart.get_day_data()
    assert chart.plot_title.endswith("February 1st")
    assert list(result.values) == [float(v) for v in range(24 * 31, 24 * 32)]

    expected_slice, expected_title = ChartSingleDay.build_day_slice(
        data_with_units=data,
        window_start=JANUARY_FIRST,
        day_in_window=31,
        timesteps_per_day=24,
        label_months_lowercase=chart.label_months_lowercase,
        title=chart.title,
    )
    assert chart.plot_title == expected_title
    assert list(result.values) == list(expected_slice.values)


def test_the_file_name_carries_the_displayed_date(tmp_path: Path) -> None:
    """The file is named by the zero-based month and day of the date shown: m0_d0 for 1 January."""
    data = _make_series(24 * 7)
    assert _chart(tmp_path, data, JANUARY_FIRST, 0).filename.startswith("days_m0_d0")
    assert _chart(tmp_path, data, JULY_FIRST, 2).filename.startswith("days_m6_d2")
