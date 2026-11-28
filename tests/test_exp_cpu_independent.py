"""The exponentials HiSim takes per time step come from math.exp, whose result does not depend on the CPU's vector unit.

``np.exp`` on a scalar runs numpy's vectorised loop. On a CPU with AVX512F (or wherever numpy's SIMD exponential is
compiled in) its last bit differs from the C library's ``exp`` on about 5 % of inputs, so a result computed with it
depends on the machine. ``math.exp`` calls the C library's ``exp``, the function Rust's ``f64::exp`` calls on Linux,
so the Rust twin of the simulation can match Python bit for bit. Bead hisim-component-physics-4g9.73.

Each test pins a function's result to the value ``math.exp`` gives, with exact equality, and makes ``np.exp`` raise
while the function runs. The second half is the guard that works on every machine: on a CPU without numpy's SIMD
exponential (the development machine of 2026-11-28 has no AVX, and there ``np.exp`` and ``math.exp`` agree on
200000 random inputs out of 200000), the values alone cannot tell the two apart.
"""

import math
from typing import Callable, List, Tuple

import numpy as np
import pytest

from hisim.components import heat_distribution_system
from hisim.components.more_advanced_heat_pump.heat_pump import MoreAdvancedHeatPumpHPLib
from hisim.simulationparameters import SimulationParameters
from hisim.subcomponents.pumps import ConstantFlowPump, PumpedWater


class ExpGuard:

    """Makes ``np.exp`` raise and records every ``math.exp`` call while a test runs."""

    @staticmethod
    def forbid_numpy_exp(monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace ``np.exp`` with a function that raises, so a call to it fails the test on every CPU.

        Args:
            monkeypatch: The test's monkeypatch fixture; it restores ``np.exp`` after the test.
        """

        def refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("np.exp was called on the time-step path; use math.exp, whose result does not depend on the CPU")

        monkeypatch.setattr(np, "exp", refuse)

    @staticmethod
    def record_math_exp(monkeypatch: pytest.MonkeyPatch) -> List[Tuple[float, float]]:
        """Wrap ``math.exp`` so that every call is recorded as ``(argument, result)``.

        Args:
            monkeypatch: The test's monkeypatch fixture; it restores ``math.exp`` after the test.

        Returns:
            The list the calls are appended to, in call order.
        """
        calls: List[Tuple[float, float]] = []
        real_exp: Callable[[float], float] = math.exp

        def recording_exp(argument: float) -> float:
            result = real_exp(argument)
            calls.append((argument, result))
            return result

        monkeypatch.setattr(math, "exp", recording_exp)
        return calls


@pytest.mark.base
@pytest.mark.parametrize("time_on_heating_in_seconds", [60.0, 600.0, 1234.5, 3600.0])
def test_the_heat_pump_start_up_ramp_is_math_exp(monkeypatch: pytest.MonkeyPatch, time_on_heating_in_seconds: float) -> None:
    """The fixed-flow start-up ramp delivers ``1 - math.exp(-t / 360 s)`` of the target power, bit for bit.

    At 0.333 kg/s over 5 K the target is 6959.7 W, above the 1800 W minimum; the outlet is the return plus the
    ramped power over ``m c``. The expected value is computed with ``math.exp``.
    """
    ExpGuard.forbid_numpy_exp(monkeypatch)
    pump = ConstantFlowPump(design_mass_flow_in_kg_per_second=0.333)
    point = MoreAdvancedHeatPumpHPLib.fixed_flow_operating_point(
        pump=pump,
        return_temperature_in_celsius=40.0,
        lift_in_kelvin=5.0,
        minimal_thermal_power_in_watt=1800.0,
        time_on_heating_in_seconds=time_on_heating_in_seconds,
    )
    target_power_in_watt = 0.333 * PumpedWater.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 5.0
    ramped_power_in_watt = target_power_in_watt * (
        1 - math.exp(-time_on_heating_in_seconds / MoreAdvancedHeatPumpHPLib.START_UP_TIME_CONSTANT_IN_SECONDS)
    )
    assert point.supply_temperature_in_celsius == PumpedWater.supply_temperature_in_celsius(
        heat_power_in_watt=ramped_power_in_watt, return_temperature_in_celsius=40.0, mass_flow_in_kg_per_second=0.333
    )


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900])
@pytest.mark.parametrize(
    ("water_input_temperature_in_celsius", "water_output_temperature_in_celsius", "thermal_power_delivered_in_watt"),
    [(35.0, 28.5, 4200.0), (21.3, 20.7, 117.25)],
)
def test_the_heat_distribution_thermal_lag_is_math_exp(
    monkeypatch: pytest.MonkeyPatch,
    seconds_per_timestep: int,
    water_input_temperature_in_celsius: float,
    water_output_temperature_in_celsius: float,
    thermal_power_delivered_in_watt: float,
) -> None:
    """Without flow the circuit's water and power decay towards the room by the factor ``math.exp(-dt / tau)``.

    The three quantities decay by the same factor, ``math.exp`` of the same argument; each result is pinned to the
    room temperature plus the previous step's difference times that factor (the power: the previous power times it).
    """
    ExpGuard.forbid_numpy_exp(monkeypatch)
    config = heat_distribution_system.HeatDistributionConfig.preset_building_derived("HeatDistributionSystem")
    config.water_mass_flow_rate_in_kg_per_second = 0.2
    config.absolute_conditioned_floor_area_in_m2 = 140.0
    config.cooling_enabled = False
    config.heating_system = heat_distribution_system.HeatDistributionSystemType.FLOORHEATING
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, seconds_per_timestep)
    )
    my_heat_distribution_system.state.water_input_temperature_in_celsius = water_input_temperature_in_celsius
    my_heat_distribution_system.state.water_output_temperature_in_celsius = water_output_temperature_in_celsius
    my_heat_distribution_system.state.thermal_power_delivered_in_watt = thermal_power_delivered_in_watt
    residence_temperature_in_celsius = 20.0

    calls = ExpGuard.record_math_exp(monkeypatch)
    inlet_in_celsius, outlet_in_celsius, power_in_watt = (
        my_heat_distribution_system.determine_water_temperature_input_output_effective_thermal_power_without_mass_flow(
            residence_temperature_in_celsius=residence_temperature_in_celsius
        )
    )

    assert len(calls) == 3
    assert len(set(calls)) == 1
    argument, decay_factor = calls[0]
    assert argument < 0
    assert decay_factor == math.exp(argument)
    assert inlet_in_celsius == residence_temperature_in_celsius + (
        (water_input_temperature_in_celsius - residence_temperature_in_celsius) * decay_factor
    )
    assert outlet_in_celsius == residence_temperature_in_celsius + (
        (water_output_temperature_in_celsius - residence_temperature_in_celsius) * decay_factor
    )
    assert power_in_watt == thermal_power_delivered_in_watt * decay_factor
