"""The hours and the quantities of Section 7.3, on synthetic time series."""

from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

from hisim.verification import aggregate, metrics
from hisim.verification.suite import RequestedOutputs

pytestmark = pytest.mark.base


def year_frame(delivered_in_watt: List[float], seconds_per_timestep: int = 3600) -> pd.DataFrame:
    """A run's frame of 2021 at the time step, the delivered power repeating the given pattern."""
    index = pd.date_range("2021-01-01", "2022-01-01", freq=f"{seconds_per_timestep}s", inclusive="left")
    pattern = np.resize(np.array(delivered_in_watt, dtype=float), len(index))
    air = 20.0 + (pattern < 0) * 7.0
    return pd.DataFrame(
        {
            metrics.AIR_TEMPERATURE_COLUMN: air,
            metrics.OPERATIVE_TEMPERATURE_COLUMN: air - 1.0,
            metrics.MASS_TEMPERATURE_COLUMN: air - 2.0,
            metrics.SOLAR_GAIN_COLUMN: np.full(len(index), 500.0),
            aggregate.DELIVERED_POWER_COLUMN: pattern,
        },
        index=index,
    )


def test_heating_and_cooling_in_one_hour_do_not_cancel() -> None:
    """Four quarter hours: two heat with 1000 W, one cools with 2000 W; the hour holds both, apart."""
    frame = pd.DataFrame(
        {aggregate.DELIVERED_POWER_COLUMN: [1000.0, 1000.0, -2000.0, 0.0, 400.0, 400.0, 400.0, 400.0], metrics.AIR_TEMPERATURE_COLUMN: [20.0] * 4 + [21.0] * 4},
        index=pd.date_range("2021-01-01", periods=8, freq="900s"),
    )

    hourly = aggregate.to_hourly(frame, 900)

    assert list(hourly.index) == [pd.Timestamp("2021-01-01 00:00"), pd.Timestamp("2021-01-01 01:00")]
    assert list(hourly[aggregate.HEATING_POWER_COLUMN]) == [500.0, 400.0]
    assert list(hourly[aggregate.COOLING_POWER_COLUMN]) == [500.0, 0.0]
    assert list(hourly[metrics.AIR_TEMPERATURE_COLUMN]) == [20.0, 21.0]
    assert aggregate.DELIVERED_POWER_COLUMN not in hourly


def test_an_hourly_run_keeps_its_hours() -> None:
    """At 3600 s the hours are the time steps; only the delivered power is split."""
    frame = year_frame([1500.0, -730.0, 0.0])

    hourly = aggregate.to_hourly(frame, 3600)

    assert len(hourly) == 8760
    assert hourly[aggregate.HEATING_POWER_COLUMN].iloc[:3].tolist() == [1500.0, 0.0, 0.0]
    assert hourly[aggregate.COOLING_POWER_COLUMN].iloc[:3].tolist() == [0.0, 730.0, 0.0]


@pytest.mark.parametrize("seconds", [0, 7, 5400])
def test_a_time_step_that_does_not_divide_the_hour_is_refused(seconds: int) -> None:
    """Hours cannot be formed from such steps."""
    frame = year_frame([0.0])

    with pytest.raises(ValueError, match="does not divide the hour"):
        aggregate.to_hourly(frame, seconds)


def test_an_incomplete_hour_is_refused() -> None:
    """Three quarter hours are not an hour."""
    frame = pd.DataFrame({aggregate.DELIVERED_POWER_COLUMN: [1.0, 1.0, 1.0]}, index=pd.date_range("2021-01-01", periods=3, freq="900s"))

    with pytest.raises(ValueError, match="has 3 time steps, not 4"):
        aggregate.to_hourly(frame, 900)


def test_the_hour_of_an_occurrence_counts_from_one() -> None:
    """Hour 1 runs from 00:00 to 01:00, so the hour labelled 23:00 on 31 December is hour 24."""
    series = pd.Series([1.0, 3.0, 2.0], index=pd.to_datetime(["2021-12-31 21:00", "2021-12-31 23:00", "2021-12-31 22:00"]))

    assert metrics.occurrence(series, largest=True) == {"value": 3.0, "at": "2021-12-31 23:00:00", "month": 12, "day": 31, "hour": 24}
    assert metrics.occurrence(series, largest=False)["hour"] == 22


def test_the_annual_and_monthly_loads_are_sums_of_the_hours() -> None:
    """One hour heats with 1500 W, one cools with 730 W, one rests, all year round."""
    hourly = aggregate.to_hourly(year_frame([1500.0, -730.0, 0.0]), 3600)

    loads = metrics.loads(hourly)

    assert loads["annual_heating_in_megawatt_hour"] == pytest.approx(2920 * 1500.0 / 1e6)
    assert loads["annual_sensible_cooling_in_megawatt_hour"] == pytest.approx(2920 * 730.0 / 1e6)
    assert loads["annual_peak_heating_in_kilowatt"] == {"value": 1.5, "at": "2021-01-01 00:00:00", "month": 1, "day": 1, "hour": 1}
    assert len(loads["monthly"]) == 12
    january = loads["monthly"][0]
    assert january["month"] == 1
    assert january["heating_in_kilowatt_hour"] == pytest.approx(248 * 1.5)
    assert january["peak_sensible_cooling_in_kilowatt"]["value"] == pytest.approx(0.73)
    assert sum(month["heating_in_kilowatt_hour"] for month in loads["monthly"]) == pytest.approx(loads["annual_heating_in_megawatt_hour"] * 1000.0)


def test_a_year_without_cooling_has_no_cooling_peak() -> None:
    """No load, no time to report."""
    hourly = aggregate.to_hourly(year_frame([1000.0]), 3600)

    assert metrics.loads(hourly)["annual_peak_sensible_cooling_in_kilowatt"] is None


def test_a_bin_is_named_by_its_lower_bound() -> None:
    """20 holds 20 <= T < 21; values outside the bins are counted apart."""
    series = pd.Series([20.0, 20.99, 19.999, -50.5, 99.0, -50.0])

    bins = metrics.bin_frequencies(series, -50, 98)

    assert bins["hours_by_bin"]["20"] == 2
    assert bins["hours_by_bin"]["19"] == 1
    assert bins["hours_by_bin"]["-50"] == 1
    assert (bins["hours_below"], bins["hours_above"]) == (1, 1)
    assert len(bins["hours_by_bin"]) == 149


def test_the_hourly_load_of_a_day_is_negative_when_it_cools() -> None:
    """Table 7-47 gives heating positive and cooling negative, 24 values in kWh."""
    hourly = aggregate.to_hourly(year_frame([1500.0, -730.0, 0.0]), 3600)

    load = metrics.daily_hourly(hourly, "load", "02-01")

    assert len(load) == 24
    assert set(load) == {1.5, -0.73, 0.0}
    with pytest.raises(ValueError, match="has 0 hours"):
        metrics.daily_hourly(hourly.iloc[:24], "load", "02-01")


def test_the_case_metrics_of_a_conditioned_case_at_fifteen_minutes() -> None:
    """Hourly quantities from quarter hours; the largest single step is kept for information."""
    frame = year_frame([2000.0, 0.0, 0.0, 0.0], seconds_per_timestep=900)
    requested = RequestedOutputs(daily_hourly=(("load", ("02-01", "07-14")),), temperature_bins_in_celsius=None)

    result: Dict = metrics.case_metrics(frame, 900, requested)

    assert (result["timesteps"], result["hours"]) == (35040, 8760)
    assert result["annual_heating_in_megawatt_hour"] == pytest.approx(8760 * 500.0 / 1e6)
    assert result["annual_peak_heating_in_kilowatt"]["value"] == pytest.approx(0.5)
    assert result["largest_time_step_heating_in_kilowatt"] == pytest.approx(2.0)
    assert result["solar_gain_through_windows_in_kilowatt_hour"] == pytest.approx(8760 * 0.5)
    assert set(result["daily_hourly"]) == {"02-01", "07-14"}
    assert result["daily_hourly"]["02-01"]["load_in_kilowatt_hour"] == [0.5] * 24
    assert "zone_air_temperature_bins" not in result


def test_the_case_metrics_of_a_free_float_case() -> None:
    """No delivered power: no loads, the zone air temperature with its bins and its day."""
    frame = year_frame([0.0]).drop(columns=[aggregate.DELIVERED_POWER_COLUMN])
    requested = RequestedOutputs(daily_hourly=(("zone_air_temperature", ("02-01",)),), temperature_bins_in_celsius=(-50, 98))

    result = metrics.case_metrics(frame, 3600, requested)

    assert "annual_heating_in_megawatt_hour" not in result
    assert result["zone_air_temperature_in_celsius"]["mean"] == pytest.approx(20.0)
    assert result["zone_air_temperature_bins"]["hours_by_bin"]["20"] == 8760
    assert result["daily_hourly"]["02-01"]["zone_air_temperature_in_celsius"] == [20.0] * 24
