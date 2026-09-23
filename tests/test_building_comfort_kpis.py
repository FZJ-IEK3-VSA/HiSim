"""The Building's two degree-hour KPIs under fixed names (hisim-sska, renovisorissues #29).

The RenoVisor comfort grades read them by name, so the names must not depend on the setpoints;
the heating sum ignores the first kelvin below the setpoint (controller ripple), and the summer sum
is taken against 26 °C, not against the cooling setpoint. The method is called
on a stand-in carrying only the attributes it reads, which keeps the test free of weather data
and sizing.
"""

from types import SimpleNamespace
from typing import Dict

import pandas as pd
import pytest

from hisim.components.building.building import Building

pytestmark = pytest.mark.base


def kpis_of(temperatures, set_heating: float = 20.0, set_cooling: float = 25.0) -> Dict[str, float]:
    """The building KPIs of an hourly indoor air temperature series, by name."""
    stand_in = SimpleNamespace(
        TemperatureIndoorAir=Building.TemperatureIndoorAir,
        UNDERHEATING_DEGREE_HOURS_KPI=Building.UNDERHEATING_DEGREE_HOURS_KPI,
        OVERHEATING_DEGREE_HOURS_KPI=Building.OVERHEATING_DEGREE_HOURS_KPI,
        OVERHEATING_THRESHOLD_IN_CELSIUS=Building.OVERHEATING_THRESHOLD_IN_CELSIUS,
        UNDERHEATING_TOLERANCE_IN_KELVIN=Building.UNDERHEATING_TOLERANCE_IN_KELVIN,
        set_heating_temperature_in_celsius=set_heating,
        set_cooling_temperature_in_celsius=set_cooling,
        seconds_per_timestep=3600,
        component_name="Building",
    )
    output = SimpleNamespace(field_name=Building.TemperatureIndoorAir)
    frame = pd.DataFrame({0: temperatures})
    entries = Building.get_building_temperature_deviation_from_set_temperatures(stand_in, output, 0, frame, [])
    return {entry.name: entry.value for entry in entries}


def test_underheating_counts_only_what_lies_more_than_1_k_below_the_own_setpoint() -> None:
    """Below a 20 °C setpoint, 19.5 °C is ripple; 18.5 °C and 17.5 °C give 0.5 + 1.5 K*h."""
    kpis = kpis_of([19.5, 18.5, 17.5, 21.0])

    assert kpis[Building.UNDERHEATING_DEGREE_HOURS_KPI] == pytest.approx(2.0)
    assert Building.UNDERHEATING_TOLERANCE_IN_KELVIN == 1.0


def test_the_name_does_not_change_with_the_setpoint() -> None:
    """At 18 °C the same series is 0.5 K*h beyond the tolerance, reported under the same name."""
    kpis = kpis_of([19.5, 18.5, 16.5, 21.0], set_heating=18.0)

    assert kpis[Building.UNDERHEATING_DEGREE_HOURS_KPI] == pytest.approx(0.5)


def test_overheating_counts_above_26_not_above_the_cooling_setpoint() -> None:
    """25.5 °C is above the 25 °C setpoint but not overheating; 27 and 28.5 °C give 1 + 2.5 K*h."""
    kpis = kpis_of([25.5, 27.0, 28.5, 22.0])

    assert kpis[Building.OVERHEATING_DEGREE_HOURS_KPI] == pytest.approx(3.5)
    assert Building.OVERHEATING_THRESHOLD_IN_CELSIUS == 26.0
