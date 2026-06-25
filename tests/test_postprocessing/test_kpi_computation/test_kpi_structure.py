"""Unit tests for the pure static helper methods of :class:`KpiHelperClass`.

Covers :meth:`compute_total_energy_from_power_timeseries` and
:meth:`calc_mean_max_min_value` in
``hisim.postprocessing.kpi_computation.kpi_structure``. Both methods are
deterministic, side-effect-free and depend only on their arguments, so they can
be tested as pure functions without any simulation setup.
"""

# clean

import pandas as pd
import pytest

from hisim.postprocessing.kpi_computation.kpi_structure import KpiHelperClass


@pytest.mark.base
def test_compute_total_energy_empty_series_returns_zero() -> None:
    """An empty power series must short-circuit to ``0.0`` kWh."""
    energy = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([], dtype=float), timeresolution=3600
    )
    assert energy == 0.0


@pytest.mark.base
def test_compute_total_energy_single_element_one_hour() -> None:
    """``[1000.0]`` W over a 3600 s step is ``1000 * 3600 / 3.6e6 = 1.0`` kWh."""
    energy = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([1000.0]), timeresolution=3600
    )
    assert energy == pytest.approx(1.0)


@pytest.mark.base
def test_compute_total_energy_two_elements_half_hour() -> None:
    """``[2000.0, 3000.0]`` W over 1800 s steps is ``5000 * 1800 / 3.6e6 = 2.5`` kWh."""
    energy = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([2000.0, 3000.0]), timeresolution=1800
    )
    assert energy == pytest.approx(2.5)


@pytest.mark.base
def test_compute_total_energy_all_zeros_returns_zero() -> None:
    """A series of zeros consumes no energy regardless of the resolution."""
    energy = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([0.0, 0.0, 0.0]), timeresolution=3600
    )
    assert energy == 0.0


@pytest.mark.base
def test_compute_total_energy_negative_power_feed_in() -> None:
    """Negative power (feed-in) yields negative energy in kWh."""
    energy = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([-1000.0]), timeresolution=3600
    )
    assert energy == pytest.approx(-1.0)


@pytest.mark.base
def test_compute_total_energy_float_timeresolution_boundary() -> None:
    """A float ``timeresolution`` of ``1.0`` s is accepted (``1000 / 3.6e6`` kWh)."""
    energy = KpiHelperClass.compute_total_energy_from_power_timeseries(
        power_timeseries_in_watt=pd.Series([1000.0]), timeresolution=1.0  # type: ignore[arg-type]
    )
    assert energy == pytest.approx(1000.0 / 3.6e6)


@pytest.mark.base
def test_calc_mean_max_min_value_from_list() -> None:
    """Mean/max/min of a plain ``list`` of ints."""
    mean_value, max_value, min_value = KpiHelperClass.calc_mean_max_min_value([1, 2, 3, 4, 5])
    assert mean_value == pytest.approx(3.0)
    assert max_value == pytest.approx(5.0)
    assert min_value == pytest.approx(1.0)


@pytest.mark.base
def test_calc_mean_max_min_value_from_series() -> None:
    """A ``pd.Series`` must give the same result as the equivalent list."""
    mean_value, max_value, min_value = KpiHelperClass.calc_mean_max_min_value(pd.Series([10, 20, 30]))
    assert mean_value == pytest.approx(20.0)
    assert max_value == pytest.approx(30.0)
    assert min_value == pytest.approx(10.0)


@pytest.mark.base
def test_calc_mean_max_min_value_single_element() -> None:
    """For a single-element collection all three statistics are equal."""
    mean_value, max_value, min_value = KpiHelperClass.calc_mean_max_min_value([5])
    assert mean_value == pytest.approx(5.0)
    assert max_value == pytest.approx(5.0)
    assert min_value == pytest.approx(5.0)


@pytest.mark.base
def test_calc_mean_max_min_value_negatives() -> None:
    """Mix of negative, zero and positive values."""
    mean_value, max_value, min_value = KpiHelperClass.calc_mean_max_min_value([-5, 0, 5])
    assert mean_value == pytest.approx(0.0)
    assert max_value == pytest.approx(5.0)
    assert min_value == pytest.approx(-5.0)


@pytest.mark.base
def test_calc_mean_max_min_value_all_equal() -> None:
    """When every element is equal, mean, max and min coincide."""
    mean_value, max_value, min_value = KpiHelperClass.calc_mean_max_min_value([7, 7, 7])
    assert mean_value == pytest.approx(7.0)
    assert max_value == pytest.approx(7.0)
    assert min_value == pytest.approx(7.0)


@pytest.mark.base
def test_calc_mean_max_min_value_returns_floats() -> None:
    """All three returned values must be plain ``float`` instances."""
    mean_value, max_value, min_value = KpiHelperClass.calc_mean_max_min_value([1, 2, 3])
    assert isinstance(mean_value, float)
    assert isinstance(max_value, float)
    assert isinstance(min_value, float)
