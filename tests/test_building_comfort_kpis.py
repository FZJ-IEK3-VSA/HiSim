"""The Building's two degree-hour KPIs under fixed names (hisim-sska, renovisorissues #29).

The RenoVisor comfort grades read them by name, so the names must not depend on the setpoints;
the heating sum ignores the first kelvin below the setpoint (controller ripple), and the summer sum
is taken against 26 °C, not against the cooling setpoint. The two sums are pure functions of a
temperature series and are tested as such; one test goes through the Building's KPI method, called
on a stand-in carrying only the attributes it reads, which keeps it free of weather data and sizing.

Scope: nothing here runs a simulation. The chain Building -> all_kpis.json -> KpiBuilder ->
result.json over a real full year is left to a slow job (bead hisim-evw9).
"""

import math
from types import SimpleNamespace
from typing import Dict, List

import pandas as pd
import pytest

from hisim.components.building.building import Building, degree_hours_above, degree_hours_below

pytestmark = pytest.mark.base

#: One hour and one quarter of an hour, in seconds.
HOURLY = 3600
QUARTER_HOURLY = 900


def kpis_of(temperatures: List[float], set_heating: float = 20.0, set_cooling: float = 25.0) -> Dict[str, float]:
    """Return the building KPIs of an hourly indoor air temperature series, by name."""
    stand_in = SimpleNamespace(
        TemperatureIndoorAir=Building.TemperatureIndoorAir,
        UNDERHEATING_DEGREE_HOURS_KPI=Building.UNDERHEATING_DEGREE_HOURS_KPI,
        OVERHEATING_DEGREE_HOURS_KPI=Building.OVERHEATING_DEGREE_HOURS_KPI,
        OVERHEATING_THRESHOLD_IN_CELSIUS=Building.OVERHEATING_THRESHOLD_IN_CELSIUS,
        UNDERHEATING_TOLERANCE_IN_KELVIN=Building.UNDERHEATING_TOLERANCE_IN_KELVIN,
        DEGREE_HOURS_UNIT=Building.DEGREE_HOURS_UNIT,
        set_heating_temperature_in_celsius=set_heating,
        set_cooling_temperature_in_celsius=set_cooling,
        seconds_per_timestep=HOURLY,
        component_name="Building",
    )
    output = SimpleNamespace(field_name=Building.TemperatureIndoorAir)
    frame = pd.DataFrame({0: temperatures})
    entries = Building.get_building_temperature_deviation_from_set_temperatures(stand_in, output, 0, frame, [])
    return {entry.name: entry.value for entry in entries}


def test_below_counts_only_what_lies_below_the_threshold() -> None:
    """Against 19 °C, 19.5 °C adds nothing; 18.5 °C and 17.5 °C add 0.5 + 1.5 K*h."""
    assert degree_hours_below(pd.Series([19.5, 18.5, 17.5, 21.0]), 19.0, HOURLY) == pytest.approx(2.0)


def test_above_counts_only_what_lies_above_the_threshold() -> None:
    """Against 26 °C, 25.5 °C adds nothing; 27 °C and 28.5 °C add 1 + 2.5 K*h."""
    assert degree_hours_above(pd.Series([25.5, 27.0, 28.5, 22.0]), 26.0, HOURLY) == pytest.approx(3.5)


def test_a_quarter_hour_step_weighs_a_quarter() -> None:
    """The same kelvins over 15-minute steps are a quarter of the hourly degree-hours."""
    series = pd.Series([27.0, 28.5, 17.5])

    assert degree_hours_above(series, 26.0, QUARTER_HOURLY) == pytest.approx(3.5 / 4)
    assert degree_hours_below(series, 19.0, QUARTER_HOURLY) == pytest.approx(1.5 / 4)


def test_a_missing_temperature_makes_the_sum_nan() -> None:
    """A gap in the series is not a comfortable hour: the sum propagates it rather than skip it."""
    series = pd.Series([27.0, float("nan"), 17.5])

    assert math.isnan(degree_hours_above(series, 26.0, HOURLY))
    assert math.isnan(degree_hours_below(series, 19.0, HOURLY))


def test_the_building_publishes_both_sums_under_names_that_ignore_its_setpoints() -> None:
    """Heating: 1 K tolerance below the own setpoint, whatever it is; summer: 26 °C, not the cooling setpoint."""
    at_20 = kpis_of([19.5, 18.5, 17.5, 25.5, 27.0, 28.5])
    at_18 = kpis_of([19.5, 18.5, 16.5, 25.5, 27.0, 28.5], set_heating=18.0)

    assert at_20[Building.UNDERHEATING_DEGREE_HOURS_KPI] == pytest.approx(0.5 + 1.5)
    assert at_18[Building.UNDERHEATING_DEGREE_HOURS_KPI] == pytest.approx(0.5)
    assert at_20[Building.OVERHEATING_DEGREE_HOURS_KPI] == pytest.approx(1.0 + 2.5)
