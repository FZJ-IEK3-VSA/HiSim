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

from hisim import loadtypes as lt
from hisim.postprocessing.charts import BarChart, Carpet, Line
from hisim.postprocessing.chartbase import AxisUnit, Chart
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
        # A full year from 1 January: a mark at every first of the month and at the end, each name centred.
        (datetime.datetime(2021, 1, 1), 365, (15.5, "Jan"), (349.5, "Dec"), "Month of the year"),
        # A leap year has one more day before March, and every month is still found.
        (datetime.datetime(2020, 1, 1), 366, (15.5, "Jan"), (350.5, "Dec"), "Month of the year"),
        # Three summer months: the names follow the window's own dates, not January's.
        (datetime.datetime(2021, 6, 1), 92, (15.0, "Jun"), (76.5, "Aug"), "Month of the year"),
        # A week in July is named by its dates.
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
    x_axis = Carpet.x_axis_ticks(window_start, number_of_days)
    assert (x_axis.label_positions[0], x_axis.labels[0]) == expected_first
    assert (x_axis.label_positions[-1], x_axis.labels[-1]) == expected_last
    assert x_axis.title == expected_title


def test_carpet_month_marks_lie_at_the_month_boundaries_and_names_between_them() -> None:
    """The marks sit where a month begins and at the window's end; each name lies halfway between two marks.

    The axis used to put one mark in the column of each month's 15th, so a reader could not see where one
    month ended and the next began.
    """
    x_axis = Carpet.x_axis_ticks(datetime.datetime(2021, 1, 1), 365)
    assert x_axis.boundaries == [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365]
    assert len(x_axis.labels) == len(x_axis.boundaries) - 1 == 12
    for start, end, position in zip(x_axis.boundaries, x_axis.boundaries[1:], x_axis.label_positions):
        assert position == (start + end) / 2


def test_a_window_that_starts_mid_month_names_its_partial_month_in_the_middle_of_what_it_covers() -> None:
    """A run from 20 March over 40 days covers 12 days of March and 28 of April: "Mar" at 6, "Apr" at 26."""
    x_axis = Carpet.x_axis_ticks(datetime.datetime(2021, 3, 20), 40)
    assert x_axis.boundaries == [0, 12, 40]
    assert x_axis.label_positions == [6.0, 26.0]
    assert x_axis.labels == ["Mar", "Apr"]


def test_carpet_x_axis_of_a_long_short_window_keeps_at_most_seven_day_ticks() -> None:
    """Twenty days get a name every third day, so the labels stay readable; every day still has its marks."""
    x_axis = Carpet.x_axis_ticks(datetime.datetime(2021, 7, 1), 20)
    assert len(x_axis.labels) <= Carpet.MAXIMUM_DAY_TICKS
    assert x_axis.labels[:2] == ["01 Jul", "04 Jul"]
    assert x_axis.boundaries == [float(day) for day in range(21)]


@pytest.mark.parametrize("timesteps_per_day", [1440, 96, 24])
def test_carpet_y_ticks_of_steps_up_to_an_hour_are_every_three_whole_hours(timesteps_per_day: int) -> None:
    """Up to an hour per step, the ticks sit every three hours of rows, 24 at the bottom."""
    rows_per_hour = timesteps_per_day // 24
    positions, labels = Carpet.y_axis_ticks(timesteps_per_day)
    assert positions == [float(hour * rows_per_hour) for hour in range(0, 25, 3)]
    assert labels == ["24:00", "21:00", "18:00", "15:00", "12:00", "09:00", "06:00", "03:00", "00:00"]


def test_carpet_y_ticks_of_four_hour_steps_lie_at_their_hours() -> None:
    """Six rows a day: 18:00 and 06:00 fall inside the rows that hold them."""
    assert Carpet.y_axis_ticks(6)[0] == [0.0, 0.75, 1.5, 2.25, 3.0, 3.75, 4.5, 5.25, 6.0]


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
        figure_format=FigureFormat.PNG,
    )

    # No directory created by the constructor even with the default checker.
    assert not target_subdir.exists()
    chart.ensure_output_dir()
    assert target_subdir.exists()


@pytest.mark.parametrize(
    ("values", "declared_unit", "expected_values", "expected_unit"),
    [
        # A power in W takes the prefix its values reach.
        ([0.0, 20914.0], "W", [0.0, 20.914], "kW"),
        ([0.0, 500.0], "W", [0.0, 500.0], "W"),
        # A unit that already carries k is not prefixed twice: 2 000 kWh are 2 MWh, never "kkWh".
        ([2000.0], "kWh", [2.0], "MWh"),
        # ... and never shown below its declared prefix: 0.5 kWh stay kWh, 0.1 kg/s stay kg/s.
        ([0.5], "kWh", [0.5], "kWh"),
        ([0.1], "kg/s", [0.1], "kg/s"),
        # Units without an SI prefix keep their unit whatever their size.
        ([2000.0], "°C", [2000.0], "°C"),
        ([5000.0], "timesteps", [5000.0], "timesteps"),
        ([2.0], "-", [2.0], "-"),
        # Seconds of three hours or more are shown in hours rather than as ks or Ms.
        ([7200.0], "s", [7200.0], "s"),
        ([2.3e6], "s", [2.3e6 / 3600.0], "h"),
        # Long unit names are shown by their symbols.
        ([900.0], "W per square meter", [900.0], "W/m²"),
        ([1400.0], "W per square meter", [1.4], "kW/m²"),
        ([350.0], "Degrees", [350.0], "°"),
        # A driven distance is shown in km once it reaches 1 000 m.
        ([12000.0], "m", [12.0], "km"),
        # NaN values do not decide the prefix.
        ([np.nan, 1500.0], "W", [np.nan, 1.5], "kW"),
    ],
)
def test_axis_unit_shows_values_in_a_readable_unit(
    values: list, declared_unit: str, expected_values: list, expected_unit: str
) -> None:
    """Each declared unit is shown with the largest prefix its values reach, never a smaller one."""
    shown_values, shown_unit = AxisUnit.scale(np.array(values), declared_unit)
    assert shown_unit == expected_unit
    np.testing.assert_allclose(shown_values, expected_values)


@pytest.mark.parametrize(
    ("declared_unit", "shown_unit", "expected"),
    [
        ("W", "kW", "Power [kW]"),
        ("Wh", "MWh", "Energy [MWh]"),
        ("°C", "°C", "Temperature [°C]"),
        ("K", "K", "Temperature difference [K]"),
        ("kg/s", "kg/s", "Mass flow [kg/s]"),
        ("s", "h", "Duration [h]"),
        ("-", "-", "Value [-]"),
    ],
)
def test_axis_label_names_the_quantity_and_the_shown_unit(declared_unit: str, shown_unit: str, expected: str) -> None:
    """An axis reads "Power [kW]" rather than a bare "[kW]"."""
    assert AxisUnit.label(declared_unit, shown_unit) == expected


def test_wrap_title_breaks_a_long_camel_case_name_and_keeps_its_words() -> None:
    """A long output name is broken at a camel-case boundary instead of running off the figure."""
    name = "WaterTemperatureAtEndOfStepWithoutSecondaryHeatGeneratorInCelsius"
    wrapped = Chart.wrap_title(f"SimpleHotWaterStorage\n{name}")
    lines = wrapped.split("\n")
    assert all(len(line) <= Chart.MAXIMUM_TITLE_CHARACTERS for line in lines)
    assert lines[0] == "SimpleHotWaterStorage"
    assert "".join(lines[1:]) == name


def test_wrap_title_keeps_a_short_title_and_the_suffix_whole() -> None:
    """A title that fits is unchanged; a suffix such as a date is never split between lines."""
    assert Chart.wrap_title("PVSystem\nElectricityOutput") == "PVSystem\nElectricityOutput"
    assert Chart.wrap_title("PVSystem\nElectricityOutput", suffix="January 1st") == (
        "PVSystem\nElectricityOutput January 1st"
    )
    long_line = "DispatchToBattery_LoadingPowerInputForTheDay"
    assert Chart.wrap_title(long_line, suffix="January 1st").rsplit("\n", maxsplit=1)[-1] == "January 1st"


class _SavedFigure(NamedTuple):
    """What a chart had drawn when it saved its figure: the axis labels, the x range and the title."""

    xlabel: str
    ylabel: str
    xlim: tuple
    title: str
    colorbar_label: str


@pytest.fixture
def saved_figures(monkeypatch: pytest.MonkeyPatch) -> list[_SavedFigure]:
    """Record the figure of every ``plt.savefig`` call instead of writing an image file."""
    import matplotlib.pyplot as plt

    figures: list[_SavedFigure] = []

    def record(*_args: object, **_kwargs: object) -> None:
        figure = plt.gcf()
        axis = figure.axes[0]
        colorbar_label = figure.axes[1].get_ylabel() if len(figure.axes) > 1 else ""
        figures.append(
            _SavedFigure(axis.get_xlabel(), axis.get_ylabel(), axis.get_xlim(), axis.get_title(), colorbar_label)
        )

    monkeypatch.setattr(plt, "savefig", record)
    return figures


def _chart_arguments(tmp_path: Path, units: str) -> dict:
    """The constructor arguments of a line or carpet chart writing below ``tmp_path``."""
    return {
        "output": "PVSystem # ElectricityOutput",
        "component_name": "PVSystem",
        "units": units,
        "directory_path": str(tmp_path),
        "output_description": "electricity output",
        "figure_format": FigureFormat.PNG,
        "path_checker": lambda **_: None,
    }


def test_line_chart_labels_the_quantity_and_ends_at_the_last_timestep(  # pylint: disable=redefined-outer-name
    tmp_path: Path, saved_figures: list[_SavedFigure]
) -> None:
    """The value axis reads "Power [kW]" and the time axis stops at the run's last timestep."""
    import matplotlib.dates as mdates

    index = pd.date_range("2021-01-01", periods=24 * 7, freq="h")
    Line(**_chart_arguments(tmp_path, "W")).plot(data_in_self_units=pd.Series(np.full(len(index), 2500.0), index=index))
    figure = saved_figures[-1]
    assert figure.ylabel == "Power [kW]"
    assert mdates.num2date(figure.xlim[1]).replace(tzinfo=None) == index[-1].to_pydatetime()


def test_carpet_shows_the_same_unit_as_the_line_chart(  # pylint: disable=redefined-outer-name
    tmp_path: Path, saved_figures: list[_SavedFigure]
) -> None:
    """A carpet of 1.2 kW is in kW (it stayed in W), and one of 2 000 kWh in MWh (it read "kkWh")."""
    start = datetime.datetime(2021, 1, 1)
    power = Carpet(**_chart_arguments(tmp_path, "W"))
    power.plot(xdims=2, data_in_self_units=pd.Series(np.full(48, 1200.0)), window_start=start)
    assert power.units == "kW"
    assert saved_figures[-1].colorbar_label == "Power [kW]"

    energy = Carpet(**_chart_arguments(tmp_path, "kWh"))
    energy.plot(xdims=2, data_in_self_units=pd.Series(np.full(48, 2000.0)), window_start=start)
    assert energy.units == "MWh"

    temperature = Carpet(**_chart_arguments(tmp_path, "°C"))
    temperature.plot(xdims=2, data_in_self_units=pd.Series(np.full(48, 2000.0)), window_start=start)
    assert temperature.units == "°C"
    assert saved_figures[-1].colorbar_label == "Temperature [°C]"


def _bar_chart(tmp_path: Path, units: str, aggregation: lt.OutputAggregation | None = None) -> BarChart:
    """A monthly bar chart of one output, writing into ``tmp_path`` only when it plots."""
    return BarChart(
        output="PVSystem # ElectricityOutput",
        component_name="PVSystem",
        units=units,
        directory_path=str(tmp_path),
        output_description="PV output",
        figure_format=FigureFormat.PNG,
        path_checker=lambda **_: None,
        aggregation=aggregation,
    )


def _plot_and_read(chart: BarChart, values: list) -> tuple:
    """Plot the chart and return its y label and the texts drawn on its axes, read just before saving."""
    import matplotlib.pyplot as plt  # pylint: disable=import-outside-toplevel

    seen: dict = {}
    original_savefig = plt.savefig

    def record(*args, **kwargs):
        seen["label"] = plt.gca().get_ylabel()
        seen["texts"] = [text.get_text() for text in plt.gca().texts]
        return original_savefig(*args, **kwargs)

    plt.savefig = record  # type: ignore[assignment]
    try:
        chart.plot(pd.Series(values))
    finally:
        plt.savefig = original_savefig  # type: ignore[assignment]
    return seen["label"], seen["texts"]


@pytest.mark.parametrize(
    ("units", "monthly_values", "expected_label"),
    [
        # A power is averaged over the month: 4,568 W are a mean of 4.568 kW, not an energy.
        ("W", [4568.0] * 12, "Power, monthly mean [kW]"),
        # An energy is summed over the month.
        ("Wh", [3_288_891.0] * 12, "Energy, monthly sum [MWh]"),
        # A temperature is averaged.
        ("°C", [21.0] * 12, "Temperature, monthly mean [°C]"),
    ],
)
def test_the_bar_chart_axis_names_mean_or_sum(
    tmp_path: Path, units: str, monthly_values: list, expected_label: str
) -> None:
    """The y axis says whether a bar is the month's mean or its sum, as the simulator aggregated it."""
    label, _texts = _plot_and_read(_bar_chart(tmp_path, units), monthly_values)
    assert label == expected_label


def test_a_running_total_is_drawn_as_its_end_of_month_value(tmp_path: Path) -> None:
    """A running total in Wh declared LAST is labelled as the value at the end of the month, not a sum."""
    label, _texts = _plot_and_read(_bar_chart(tmp_path, "Wh", lt.OutputAggregation.LAST), [1000.0 * m for m in range(12)])
    assert label == "Energy, end of month [kWh]"


def test_a_stored_energy_declared_mean_is_drawn_as_a_monthly_mean(tmp_path: Path) -> None:
    """A stored energy in Wh declared MEAN overrides the sum its unit would get."""
    label, _texts = _plot_and_read(_bar_chart(tmp_path, "Wh", lt.OutputAggregation.MEAN), [500.0] * 12)
    assert label == "Energy, monthly mean [Wh]"


def test_an_output_without_aggregate_gets_no_bar_chart(tmp_path: Path) -> None:
    """An output declared NONE has no monthly values, so a bar chart of it is refused."""
    with pytest.raises(ValueError, match="none"):
        _bar_chart(tmp_path, "-", lt.OutputAggregation.NONE)


def test_a_bar_chart_of_zeros_says_so(tmp_path: Path) -> None:
    """An output that is 0 in every month gets a note instead of an empty axis."""
    _label, texts = _plot_and_read(_bar_chart(tmp_path, "Wh"), [0.0] * 12)
    assert texts == ["All monthly values are 0"]
