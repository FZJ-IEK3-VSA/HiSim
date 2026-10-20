"""The hplib heat pump keeps its configured minimal thermal power for space heating after a hot-water step.

A hot-water step modulates down to no minimal power, a space-heating step to the configured
``minimum_thermal_output_power_in_watt``. The heat pump used to set its minimum to 0 W in its first hot-water step and
never back, so from then on every space-heating step modulated below the configured minimum as well.
"""

from typing import Dict, List

import numpy as np
import pytest

from hisim.components import more_advanced_heat_pump_hplib
from hisim.components.more_advanced_heat_pump_hplib import (
    MoreAdvancedHeatPumpHPLib,
    PositionHotWaterStorageInSystemSetup,
)
from hisim.components.more_advanced_heat_pump_hplib_model import (
    HeatPumpRunTimers,
    HeatPumpStepConditions,
    HplibResult,
)
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


class MinimumRecordingHeatPump(MoreAdvancedHeatPumpHPLib):
    """An hplib heat pump that records the minimal thermal power of every hplib request, per circuit.

    The recording shows which minimum each circuit hands to hplib; the answer itself is the real heat pump's.
    """

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        """Build the heat pump and an empty record of the minima, keyed by hplib's operation mode."""
        super().__init__(*args, **kwargs)
        self.minimal_thermal_powers_in_watt: Dict[str, List[float]] = {}

    def get_cached_results_or_run_hplib_simulation(  # type: ignore[override]
        self, *, operation_mode: str, minimal_thermal_power_in_watt: float, **kwargs
    ) -> HplibResult:
        """Record the minimal thermal power of this request under its operation mode, then answer it.

        Args:
            operation_mode: The circuit hplib is evaluated for.
            minimal_thermal_power_in_watt: The minimal thermal power of the request, in W.
            **kwargs: The request's temperatures and hplib mode.

        Returns:
            The real heat pump's answer.
        """
        self.minimal_thermal_powers_in_watt.setdefault(operation_mode, []).append(minimal_thermal_power_in_watt)
        return super().get_cached_results_or_run_hplib_simulation(
            operation_mode=operation_mode, minimal_thermal_power_in_watt=minimal_thermal_power_in_watt, **kwargs
        )


def heat_pump_with_hot_water(position: PositionHotWaterStorageInSystemSetup) -> MinimumRecordingHeatPump:
    """Build an 8 kW air-water heat pump with hot-water preparation and a 1800 W minimum, at 60 s steps.

    Args:
        position: Where its space-heating buffer sits, which selects the parallel or the fixed-flow mode.

    Returns:
        The heat pump, recording the minimum of every hplib request.
    """
    config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
    config.set_thermal_output_power_in_watt = 8000.0
    config.heating_reference_temperature_in_celsius = -7.0
    config.with_domestic_hot_water_preparation = True
    config.minimum_thermal_output_power_in_watt = 1800.0
    config.position_hot_water_storage_in_system = position
    return MinimumRecordingHeatPump(
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60), config=config
    )


def mild_day_conditions() -> HeatPumpStepConditions:
    """Return a mild day's step: 10 °C outside and at the source, a space-heating return 0.5 K below its set point.

    At the fixed flow of the 8 kW machine the 0.5 K lift carries about 800 W, well below the 1800 W minimum.
    """
    return HeatPumpStepConditions(
        source_temperature_in_celsius=10.0,
        ambient_temperature_in_celsius=10.0,
        space_heating_return_temperature_in_celsius=30.0,
        hot_water_return_temperature_in_celsius=45.0,
        space_heating_set_temperature_in_celsius=30.5,
        hot_water_supply_set_temperature_in_celsius=55.0,
    )


def running_for_an_hour() -> HeatPumpRunTimers:
    """Return the timers of a machine that has been heating for an hour, so its start-up has long settled."""
    return HeatPumpRunTimers(time_on_heating_in_seconds=3600, time_on_cooling_in_seconds=0, time_off_in_seconds=0)


@pytest.mark.parametrize("position", list(PositionHotWaterStorageInSystemSetup))
def test_space_heating_after_hot_water_asks_hplib_with_the_configured_minimum(
    position: PositionHotWaterStorageInSystemSetup,
) -> None:
    """Catches a hot-water step that leaves its 0 W minimum behind for the space-heating steps after it."""
    heat_pump = heat_pump_with_hot_water(position)
    conditions = mild_day_conditions()

    heat_pump.hot_water(conditions, running_for_an_hour())
    heat_pump.space_heating(conditions, running_for_an_hour())
    heat_pump.hot_water(conditions, running_for_an_hour())

    recorded = heat_pump.minimal_thermal_powers_in_watt
    assert set(recorded["heating_building"]) == {1800.0}
    assert set(recorded["heating_dhw"]) == {0.0}


def test_fixed_flow_space_heating_after_hot_water_delivers_the_configured_minimum() -> None:
    """Catches space heating that modulates below its minimum once the machine has heated hot water.

    In the fixed-flow mode the target power is the heat the nominal flow carries over the lift, at least the
    minimum; the 0.5 K lift carries about 800 W, so a machine that has run for an hour delivers
    ``1800 W * (1 - e^(-3600 / 360))``, about 1799.9 W, before and after a hot-water step alike.
    """
    heat_pump = heat_pump_with_hot_water(PositionHotWaterStorageInSystemSetup.SERIES)
    conditions = mild_day_conditions()
    expected_thermal_power_in_watt = 1800.0 * (
        1 - np.exp(-3600 / MoreAdvancedHeatPumpHPLib.START_UP_TIME_CONSTANT_IN_SECONDS)
    )

    before_hot_water = heat_pump.space_heating(conditions, running_for_an_hour())
    heat_pump.hot_water(conditions, running_for_an_hour())
    after_hot_water = heat_pump.space_heating(conditions, running_for_an_hour())

    assert before_hot_water.thermal_power_space_heating_in_watt == pytest.approx(expected_thermal_power_in_watt)
    assert after_hot_water.thermal_power_space_heating_in_watt == pytest.approx(expected_thermal_power_in_watt)
