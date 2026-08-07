"""Unit tests for the pure static helper methods of :class:`KpiHelperClass`.

Covers :meth:`compute_total_energy_from_power_timeseries` and
:meth:`compute_mean_max_min_values` in
``hisim.postprocessing.kpi_computation.kpi_structure``. Both methods are
deterministic, side-effect-free and depend only on their arguments, so they can
be tested as pure functions without any simulation setup.
"""

# clean

import numpy as np
import pandas as pd
import pytest

from hisim.postprocessing.kpi_computation.kpi_structure import KpiHelperClass


@pytest.mark.base
def test_compute_total_energy_empty_series_returns_zero() -> None:
    """An empty power series must short-circuit to ``0.0`` kWh."""
    energy_in_kwh = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([], dtype=float),
        time_resolution_in_seconds=3600,
    )
    assert energy_in_kwh == 0.0


@pytest.mark.base
def test_compute_total_energy_two_equal_elements_one_hour() -> None:
    """``[1000, 1000]`` W over 3600 s steps is ``2000 * 3600 / 3.6e6 = 2.0`` kWh."""
    energy_in_kwh = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([1000, 1000]),
        time_resolution_in_seconds=3600,
    )
    assert energy_in_kwh == pytest.approx(2.0)


@pytest.mark.base
def test_compute_total_energy_single_element_one_hour() -> None:
    """``[1000]`` W over a 3600 s step is ``1000 * 3600 / 3.6e6 = 1.0`` kWh."""
    energy_in_kwh = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([1000]),
        time_resolution_in_seconds=3600,
    )
    assert energy_in_kwh == pytest.approx(1.0)


@pytest.mark.base
def test_compute_total_energy_all_zeros_returns_zero() -> None:
    """A series of zeros consumes no energy regardless of the resolution."""
    energy_in_kwh = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([0, 0, 0]),
        time_resolution_in_seconds=3600,
    )
    assert energy_in_kwh == 0.0


@pytest.mark.base
def test_compute_total_energy_net_zero_with_negative_power() -> None:
    """Negative power (feed-in) is allowed; equal and opposite values net to zero."""
    energy_in_kwh = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([1000, -1000]),
        time_resolution_in_seconds=3600,
    )
    assert energy_in_kwh == pytest.approx(0.0)


@pytest.mark.base
def test_compute_total_energy_half_hour_steps() -> None:
    """``[500, 1000, 1500]`` W over 1800 s steps is ``3000 * 1800 / 3.6e6 = 1.5`` kWh."""
    energy_in_kwh = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([500, 1000, 1500]),
        time_resolution_in_seconds=1800,
    )
    assert energy_in_kwh == pytest.approx(1.5)


@pytest.mark.base
def test_compute_total_energy_skips_nan_values() -> None:
    """``pd.Series.sum`` skips NaN by default, so ``[1000, NaN, 1000]`` sums to 2000 W."""
    energy_in_kwh = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([1000, np.nan, 1000]),
        time_resolution_in_seconds=3600,
    )
    assert energy_in_kwh == pytest.approx(2.0)


@pytest.mark.base
def test_compute_mean_max_min_values_from_list() -> None:
    """Mean/max/min of a plain ``list`` of ints."""
    mean_value, max_value, min_value = KpiHelperClass.compute_mean_max_min_values([1, 2, 3])
    assert mean_value == pytest.approx(2.0)
    assert max_value == pytest.approx(3.0)
    assert min_value == pytest.approx(1.0)


@pytest.mark.base
def test_compute_mean_max_min_values_single_element() -> None:
    """For a single-element collection all three statistics are equal."""
    mean_value, max_value, min_value = KpiHelperClass.compute_mean_max_min_values([5])
    assert mean_value == pytest.approx(5.0)
    assert max_value == pytest.approx(5.0)
    assert min_value == pytest.approx(5.0)


@pytest.mark.base
def test_compute_mean_max_min_values_all_negative() -> None:
    """All-negative input: mean is the middle value, max is the least negative."""
    mean_value, max_value, min_value = KpiHelperClass.compute_mean_max_min_values([-1, -2, -3])
    assert mean_value == pytest.approx(-2.0)
    assert max_value == pytest.approx(-1.0)
    assert min_value == pytest.approx(-3.0)


@pytest.mark.base
def test_compute_mean_max_min_values_from_series() -> None:
    """A ``pd.Series`` must give the same result as the equivalent list."""
    mean_value, max_value, min_value = KpiHelperClass.compute_mean_max_min_values(
        pd.Series([10, 20, 30])
    )
    assert mean_value == pytest.approx(20.0)
    assert max_value == pytest.approx(30.0)
    assert min_value == pytest.approx(10.0)


@pytest.mark.base
def test_compute_mean_max_min_values_zero_boundary() -> None:
    """A single zero element yields ``(0.0, 0.0, 0.0)``."""
    mean_value, max_value, min_value = KpiHelperClass.compute_mean_max_min_values([0])
    assert mean_value == pytest.approx(0.0)
    assert max_value == pytest.approx(0.0)
    assert min_value == pytest.approx(0.0)
