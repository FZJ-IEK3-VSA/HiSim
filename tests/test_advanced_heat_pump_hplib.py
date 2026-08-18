"""Test for advanced heat pump hplib."""

import pandas as pd
import pytest
from tests import functions_for_testing as fft
from hisim import component as cp
from hisim.components.advanced_heat_pump_hplib import (
    HeatPumpHplib,
    HeatPumpHplibConfig,
    HeatPumpState,
)
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.units import Quantity, Watt, Celsius, Seconds, Kilogram, Euro, Years, Unitless
from hisim.component import ComponentID


def _make_heatpump_instance() -> HeatPumpHplib:
    """Build a HeatPumpHplib without running the heavy __init__.

    get_heatpump_cycles only reads the class attribute TimeOff, so bypassing
    __init__ keeps the test fast and isolated from the hplib parameter database.
    """
    return HeatPumpHplib.__new__(HeatPumpHplib)


class _RaisingValue:
    """Value whose equality comparison raises a non-IndexError.

    Used to verify that get_heatpump_cycles surfaces real failures instead of
    silently swallowing every exception as the previous broad ``except Exception`` did.
    """

    def __eq__(self, other: object) -> bool:
        raise TypeError("comparison failure should propagate")


@pytest.mark.base
def test_heat_pump_hplib() -> None:
    """Simulate one timestep of a generic hplib heat pump and verify outputs.

    Constructs a `HeatPumpHplib` with a generic model, a 10 kW thermal setpoint,
    -7 °C source / 52 °C sink temperatures, and a one-day simulation at 60 s
    resolution. Feeds fake component outputs (on/off, primary/secondary inlet
    temperatures, ambient temperature) through `i_simulate` and asserts that
    the resulting thermal power, electrical power, COP, output temperature,
    mass flow, and state timers match expected deterministic values.
    """

    # Definitions for HeatPump init
    model: str = "Generic"
    group_id: int = 1
    t_in: Quantity[float, Celsius] = Quantity(-7, Celsius)
    t_out: Quantity[float, Celsius] = Quantity(52, Celsius)
    p_th_set: Quantity[float, Watt] = Quantity(10000, Watt)
    simpars: SimulationParameters = SimulationParameters.one_day_only(2017, 60)
    # Definitions for i_simulate
    timestep: int = 1
    force_convergence: bool = False

    # Create fake component outputs as inputs for simulation
    on_off_switch: cp.ComponentOutput = cp.ComponentOutput("Fake_on_off_switch", "Fake_on_off_switch", lt.LoadTypes.ANY, lt.Units.ANY)
    t_in_primary: cp.ComponentOutput = cp.ComponentOutput("Fake_t_in_primary", "Fake_t_in_primary", lt.LoadTypes.ANY, lt.Units.ANY)
    t_in_secondary: cp.ComponentOutput = cp.ComponentOutput("Fake_t_in_secondary", "Fake_t_in_secondary", lt.LoadTypes.ANY, lt.Units.ANY)
    t_amb: cp.ComponentOutput = cp.ComponentOutput("Fake_t_amb", "Fake_t_amb", lt.LoadTypes.ANY, lt.Units.ANY)

    # Initialize component
    heatpump_config: HeatPumpHplibConfig = HeatPumpHplibConfig(
        component_id=ComponentID(name="Heat Pump"),
        model=model,
        group_id=group_id,
        heating_reference_temperature_in_celsius=t_in,
        flow_temperature_in_celsius=t_out,
        set_thermal_output_power_in_watt=p_th_set,
        cycling_mode=True,
        minimum_idle_time_in_seconds=Quantity(600, Seconds),
        minimum_running_time_in_seconds=Quantity(600, Seconds),
        device_co2_footprint_in_kg=Quantity(p_th_set.value * 1e-3 * 165.84, Kilogram),
        investment_costs_in_euro=Quantity(p_th_set.value * 1e-3 * 1513.74, Euro),
        lifetime_in_years=Quantity(10, Years),
        maintenance_costs_in_euro_per_year=Quantity(0.025 * p_th_set.value * 1e-3 * 1513.74, Euro),
        subsidy_as_percentage_of_investment_costs=Quantity(0.3, Unitless)
    )
    heatpump: HeatPumpHplib = HeatPumpHplib(config=heatpump_config, my_simulation_parameters=simpars)
    heatpump.state = HeatPumpState(time_on_heating=0, time_off=0, time_on_cooling=0, on_off_previous=1)

    number_of_outputs: int = fft.get_number_of_outputs([on_off_switch, t_in_primary, t_in_secondary, t_amb, heatpump])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    heatpump.on_off_switch.source_output = on_off_switch
    heatpump.t_in_primary.source_output = t_in_primary
    heatpump.t_in_secondary.source_output = t_in_secondary
    heatpump.t_amb.source_output = t_amb

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([on_off_switch, t_in_primary, t_in_secondary, t_amb, heatpump])
    stsv.values[on_off_switch.global_index] = 1
    stsv.values[t_in_primary.global_index] = -7
    stsv.values[t_in_secondary.global_index] = 47.0
    stsv.values[t_amb.global_index] = -7

    # Simulation
    heatpump.i_simulate(timestep=timestep, stsv=stsv, force_convergence=force_convergence)
    log.information(str(stsv.values))
    # Check
    assert p_th_set.value == stsv.values[heatpump.p_th.global_index]
    assert 7074.033573088874 == stsv.values[heatpump.p_el.global_index]
    assert 1.4136206588052005 == stsv.values[heatpump.cop.global_index]
    assert t_out.value == stsv.values[heatpump.t_out.global_index]
    assert 0.47619047619047616 == stsv.values[heatpump.m_dot.global_index]
    assert 60 == stsv.values[heatpump.time_on_heating.global_index]
    assert 0 == stsv.values[heatpump.time_off.global_index]


@pytest.mark.base
@pytest.mark.parametrize(
    "time_off_series, expected_cycles",
    [
        ([0, 0, 5, 5, 0, 3, 0, 0], 2),
        ([5, 0, 3], 1),
        ([5], 0),
        ([0], 0),
        ([], 0),
    ],
    ids=["two_transitions", "trailing_nonzero", "single_nonzero", "single_zero", "empty"],
)
def test_get_heatpump_cycles_counts_off_to_on_transitions(
    time_off_series: list[int], expected_cycles: int
) -> None:
    """Cycle count equals the number of off (!= 0) -> on (== 0) transitions.

    A cycle is counted when an off period (TimeOff != 0) is immediately followed
    by an on step (TimeOff == 0). The look-ahead at the final element raises
    IndexError, which must be caught so the loop terminates cleanly.
    """
    instance = _make_heatpump_instance()
    postprocessing_results = pd.DataFrame(
        {"other": [0] * len(time_off_series), "TimeOff": time_off_series}
    )
    output = cp.ComponentOutput("Heat Pump", HeatPumpHplib.TimeOff, lt.LoadTypes.TIME, lt.Units.SECONDS)
    assert instance.get_heatpump_cycles(
        output=output, index=1, postprocessing_results=postprocessing_results
    ) == expected_cycles


@pytest.mark.base
def test_get_heatpump_cycles_ignores_non_timeoff_output() -> None:
    """Outputs whose field_name is not TimeOff contribute no cycles."""
    instance = _make_heatpump_instance()
    # [5, 0, 5, 0] would yield 2 cycles for a TimeOff output; a non-TimeOff output must return 0.
    postprocessing_results = pd.DataFrame({"ThermalOutputPower": [5, 0, 5, 0]})
    output = cp.ComponentOutput(
        "Heat Pump", HeatPumpHplib.ThermalOutputPower, lt.LoadTypes.HEATING, lt.Units.WATT
    )
    assert instance.get_heatpump_cycles(
        output=output, index=0, postprocessing_results=postprocessing_results
    ) == 0


@pytest.mark.base
def test_get_heatpump_cycles_propagates_non_index_errors() -> None:
    """Non-IndexError exceptions must propagate instead of being silently swallowed.

    Regression test for the broad ``except Exception`` that previously masked real
    failures (e.g. a TypeError from malformed data). Here the look-ahead
    ``values[time_index + 1] == 0`` raises TypeError on an interior element, which
    must surface rather than corrupting the cycle count.
    """
    instance = _make_heatpump_instance()
    # index 0 is non-zero so the look-ahead to values[1] is evaluated; values[1] is
    # _RaisingValue, whose == raises TypeError (not IndexError).
    postprocessing_results = pd.DataFrame(
        {"other": [0, 0, 0], "TimeOff": [5, _RaisingValue(), 0]}
    )
    output = cp.ComponentOutput("Heat Pump", HeatPumpHplib.TimeOff, lt.LoadTypes.TIME, lt.Units.SECONDS)
    with pytest.raises(TypeError, match="comparison failure should propagate"):
        instance.get_heatpump_cycles(
            output=output, index=1, postprocessing_results=postprocessing_results
        )


@pytest.mark.base
def test_heatpump_display_config_instance_isolation() -> None:
    """Each call to HeatPumpHplib.__init__ without display_config gets a fresh instance.

    Regression test: mutable default argument DisplayConfig() would create a
    shared instance, so mutations by one caller would affect subsequent calls.
    """
    simpars = SimulationParameters.one_day_only(2017, 60)
    hp_config = HeatPumpHplibConfig.get_default_generic_advanced_hp_lib()

    hp1 = HeatPumpHplib(
        my_simulation_parameters=simpars,
        config=hp_config,
    )
    hp2 = HeatPumpHplib(
        my_simulation_parameters=simpars,
        config=hp_config,
    )

    # Identity check: they must NOT be the same object
    assert hp1.my_display_config is not hp2.my_display_config
    assert isinstance(hp1.my_display_config, cp.DisplayConfig)
    assert isinstance(hp2.my_display_config, cp.DisplayConfig)
