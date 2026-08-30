"""Test for advanced heat pump hplib."""

import numpy as np
import pandas as pd
import pytest
from tests import functions_for_testing as fft
from hisim import component as cp
from hisim.components.more_advanced_heat_pump_hplib import (
    PositionHotWaterStorageInSystemSetup,
    MoreAdvancedHeatPumpHPLib,
    MoreAdvancedHeatPumpHPLibConfig,
    MoreAdvancedHeatPumpHPLibState,
)
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.config import ComponentID

# Heat pump configuration constants
CO2_FOOTPRINT_COEFFICIENT: float = 165.84  # kg CO2 per kW thermal power over lifetime
INVESTMENT_COST_COEFFICIENT: float = 1513.74  # EUR per kW thermal power
MAINTENANCE_COST_FRACTION: float = 0.025  # Fraction of investment cost per year


@pytest.mark.base
def test_heat_pump_hplib_new() -> None:
    """Test MoreAdvancedHeatPumpHPLib with a generic model in heating mode.

    Verifies that the heat pump correctly computes thermal power output,
    electrical power consumption, COP, flow temperature, mass flow rate,
    and state timers when operating in space heating mode with DHW preparation
    enabled.
    """

    # Definitions for HeatPump init
    model: str = "Generic"
    group_id: int = 1
    t_in: float = -7
    t_out: float = 52
    p_th_set: float = 10000
    with_domestic_hot_water_preparation: bool = True
    simpars = SimulationParameters.one_day_only(2017, 60)
    # Definitions for i_simulate
    timestep = 1
    force_convergence = False

    # Create fake component outputs as inputs for simulation
    on_off_switch_sh = cp.ComponentOutput(
        "Fake_on_off_switch",
        "Fake_on_off_switch",
        lt.LoadTypes.ANY,
        lt.Units.ANY,
        component_id=ComponentID("Fake_on_off_switch"),
    )
    on_off_switch_dhw = cp.ComponentOutput(
        "Fake_on_off_switch",
        "Fake_on_off_switch",
        lt.LoadTypes.ANY,
        lt.Units.ANY,
        component_id=ComponentID("Fake_on_off_switch"),
    )
    const_thermal_power_value_dhw = cp.ComponentOutput(
        "Fake_const_thermal_power_value_dhw",
        "Fake_const_thermal_power_value_dhw",
        lt.LoadTypes.ANY,
        lt.Units.ANY,
        component_id=ComponentID("Fake_const_thermal_power_value_dhw"),
    )
    t_in_primary = cp.ComponentOutput(
        "Fake_t_in_primary",
        "Fake_t_in_primary",
        lt.LoadTypes.ANY,
        lt.Units.ANY,
        component_id=ComponentID("Fake_t_in_primary"),
    )
    t_in_secondary_sh = cp.ComponentOutput(
        "Fake_t_in_secondary_hot_water",
        "Fake_t_in_secondary_hot_water",
        lt.LoadTypes.ANY,
        lt.Units.ANY,
        component_id=ComponentID("Fake_t_in_secondary_hot_water"),
    )
    t_in_secondary_dhw = cp.ComponentOutput(
        "Fake_t_in_secondary_dhw",
        "Fake_t_in_secondary_dhw",
        lt.LoadTypes.ANY,
        lt.Units.ANY,
        component_id=ComponentID("Fake_t_in_secondary_dhw"),
    )
    t_amb = cp.ComponentOutput(
        "Fake_t_amb", "Fake_t_amb", lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake_t_amb")
    )

    # Initialize component
    heatpump_config = MoreAdvancedHeatPumpHPLibConfig(
        component_id=ComponentID(name="Heat Pump"),
        model=model,
        fluid_primary_side="air",
        group_id=group_id,
        heating_reference_temperature_in_celsius=t_in,
        flow_temperature_in_celsius=t_out,
        set_thermal_output_power_in_watt=p_th_set,
        cycling_mode=True,
        minimum_idle_time_in_seconds=600,
        minimum_running_time_in_seconds=600,
        minimum_thermal_output_power_in_watt=1500,
        position_hot_water_storage_in_system=PositionHotWaterStorageInSystemSetup.PARALLEL,
        with_domestic_hot_water_preparation=with_domestic_hot_water_preparation,
        passive_cooling_with_brine=False,
        electrical_input_power_brine_pump_in_watt=None,
        massflow_nominal_secondary_side_in_kg_per_s=0.333,
        specific_heat_capacity_of_primary_fluid=0,
        device_co2_footprint_in_kg=p_th_set * 1e-3 * CO2_FOOTPRINT_COEFFICIENT,
        investment_costs_in_euro=p_th_set * 1e-3 * INVESTMENT_COST_COEFFICIENT,
        lifetime_in_years=10,
        maintenance_costs_in_euro_per_year=MAINTENANCE_COST_FRACTION * p_th_set * 1e-3 * INVESTMENT_COST_COEFFICIENT,
        subsidy_as_percentage_of_investment_costs=0.3,
    )
    heatpump = MoreAdvancedHeatPumpHPLib(config=heatpump_config, my_simulation_parameters=simpars)
    heatpump.state = MoreAdvancedHeatPumpHPLibState(
        time_on_heating=0,
        time_off=0,
        time_on_cooling=0,
        on_off_previous=1,
        cumulative_electrical_energy_tot_in_watt_hour=0,
        cumulative_thermal_energy_tot_in_watt_hour=0,
        cumulative_thermal_energy_sh_in_watt_hour=0,
        cumulative_thermal_energy_dhw_in_watt_hour=0,
        cumulative_electrical_energy_sh_in_watt_hour=0,
        cumulative_electrical_energy_dhw_in_watt_hour=0,
        counter_switch_sh=0,
        counter_switch_dhw=0,
        counter_onoff=0,
        delta_t_secondary_side=5,
        delta_t_primary_side=5,
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            on_off_switch_sh,
            on_off_switch_dhw,
            const_thermal_power_value_dhw,
            t_in_primary,
            t_in_secondary_sh,
            t_in_secondary_dhw,
            t_amb,
            heatpump,
        ]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    heatpump.on_off_switch_sh.source_output = on_off_switch_sh
    heatpump.on_off_switch_dhw.source_output = on_off_switch_dhw
    heatpump.const_thermal_power_value_dhw.source_output = const_thermal_power_value_dhw
    heatpump.t_in_primary.source_output = t_in_primary
    heatpump.t_in_secondary_sh.source_output = t_in_secondary_sh
    heatpump.t_in_secondary_dhw.source_output = t_in_secondary_dhw
    heatpump.t_amb.source_output = t_amb

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [
            on_off_switch_sh,
            on_off_switch_dhw,
            const_thermal_power_value_dhw,
            t_in_primary,
            t_in_secondary_sh,
            t_in_secondary_dhw,
            t_amb,
            heatpump,
        ]
    )
    stsv.values[on_off_switch_sh.global_index] = 1
    stsv.values[on_off_switch_dhw.global_index] = 0
    stsv.values[const_thermal_power_value_dhw.global_index] = 0
    stsv.values[t_in_primary.global_index] = -7
    stsv.values[t_in_secondary_sh.global_index] = 47.0
    stsv.values[t_in_secondary_dhw.global_index] = 55.0
    stsv.values[t_amb.global_index] = -7

    # Simulation
    heatpump.i_simulate(timestep=timestep, stsv=stsv, force_convergence=force_convergence)
    # Check
    assert p_th_set == stsv.values[heatpump.p_th_sh.global_index]
    assert 7074.033573088874 == stsv.values[heatpump.p_el_sh.global_index]
    assert 1.4136206588052005 == stsv.values[heatpump.cop.global_index]
    assert t_out == stsv.values[heatpump.t_out_sh.global_index]
    assert 0.47619047619047616 == stsv.values[heatpump.m_dot_sh.global_index]
    assert 60 == stsv.values[heatpump.time_on_heating.global_index]
    assert 0 == stsv.values[heatpump.time_off.global_index]


@pytest.mark.base
def test_get_heatpump_cycles_counts_transitions() -> None:
    """get_heatpump_cycles counts off->on transitions and tolerates the last-element boundary.

    A cycle is registered when the current ``TimeOff`` value is non-zero and the
    following value is zero (i.e. the heat pump turns back on). The trailing
    element has no successor and must not raise.
    """
    simpars = SimulationParameters.one_day_only(2017, 60)
    config = MoreAdvancedHeatPumpHPLibConfig.get_default_generic_advanced_hp_lib()
    heatpump = MoreAdvancedHeatPumpHPLib(config=config, my_simulation_parameters=simpars)

    time_off_output = cp.ComponentOutput(
        "HeatPump", heatpump.TimeOff, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("HeatPump")
    )
    other_output = cp.ComponentOutput(
        "HeatPump",
        heatpump.ThermalOutputPowerSH,
        lt.LoadTypes.HEATING,
        lt.Units.WATT,
        component_id=ComponentID("HeatPump"),
    )

    # Column 0 holds the TimeOff series; an unrelated column sits at index 1.
    # Series: 0, 60, 60, 0, 120, 0  -> transitions at indices 2->3 and 4->5 => 2 cycles.
    postprocessing_results = pd.DataFrame(
        {
            heatpump.TimeOff: [0, 60, 60, 0, 120, 0],
            "other": [1, 2, 3, 4, 5, 6],
        }
    )
    cycles = heatpump.get_heatpump_cycles(
        output=time_off_output, index=0, postprocessing_results=postprocessing_results
    )
    assert cycles == 2

    # Single-element series: the only element has no successor; must not raise.
    single = pd.DataFrame({heatpump.TimeOff: [60]})
    assert heatpump.get_heatpump_cycles(output=time_off_output, index=0, postprocessing_results=single) == 0

    # No transitions when the series never returns to zero after a non-zero value.
    always_on_then_off = pd.DataFrame({heatpump.TimeOff: [0, 60, 60, 60]})
    assert heatpump.get_heatpump_cycles(output=time_off_output, index=0, postprocessing_results=always_on_then_off) == 0

    # When the output field is not TimeOff, the method short-circuits to 0.
    assert (
        heatpump.get_heatpump_cycles(output=other_output, index=0, postprocessing_results=postprocessing_results) == 0
    )


def _running_streak_counter(is_active_profile: list, seconds_per_timestep: int) -> list:
    """Build a counter like TimeOnHeating/TimeOnCooling: increments while active, resets to 0 when idle."""
    counter = 0
    values = []
    for is_active in is_active_profile:
        counter = counter + seconds_per_timestep if is_active else 0
        values.append(counter)
    return values


@pytest.mark.base
@pytest.mark.parametrize(
    "is_active_profile",
    [
        [0] * 1440,
        [1] * 1440,
        [1] * 700 + [0] * 100 + [1] * 200 + [0] * 440,
    ],
    ids=["never_active", "always_active", "two_active_periods"],
)
def test_get_component_kpi_entries_heating_and_cooling_hours_match_active_time(is_active_profile: list) -> None:
    """Heating/cooling hours must equal the actual time the heat pump was on."""
    simpars = SimulationParameters.one_day_only(2017, 60)
    config = MoreAdvancedHeatPumpHPLibConfig.get_default_generic_advanced_hp_lib()
    heatpump = MoreAdvancedHeatPumpHPLib(config=config, my_simulation_parameters=simpars)

    streak = _running_streak_counter(is_active_profile, simpars.seconds_per_timestep)
    heating_output = cp.ComponentOutput(heatpump.component_name, heatpump.TimeOnHeating, lt.LoadTypes.TIME, lt.Units.SECONDS)
    cooling_output = cp.ComponentOutput(heatpump.component_name, heatpump.TimeOnCooling, lt.LoadTypes.TIME, lt.Units.SECONDS)
    postprocessing_results = pd.DataFrame({heatpump.TimeOnHeating: streak, heatpump.TimeOnCooling: streak})

    kpi_entries = heatpump.get_component_kpi_entries(
        all_outputs=[heating_output, cooling_output], postprocessing_results=postprocessing_results
    )
    heating_hours = next(e for e in kpi_entries if e.name == "Heating hours of SH heat pump").value
    cooling_hours = next(e for e in kpi_entries if e.name == "Cooling hours of SH heat pump").value

    expected_hours = sum(is_active_profile) * simpars.seconds_per_timestep / 3600
    assert heating_hours == pytest.approx(expected_hours)
    assert cooling_hours == pytest.approx(expected_hours)


@pytest.mark.base
def test_get_heatpump_cycles_propagates_non_index_errors() -> None:
    """get_heatpump_cycles no longer swallows non-IndexError exceptions.

    Before narrowing the ``except`` clause to ``IndexError``, any exception
    (including ``ValueError``/``TypeError`` from unexpected data) was silently
    swallowed. Such errors must now propagate so real bugs surface rather than
    being masked as a silently-wrong cycle count.
    """
    simpars = SimulationParameters.one_day_only(2017, 60)
    config = MoreAdvancedHeatPumpHPLibConfig.get_default_generic_advanced_hp_lib()
    heatpump = MoreAdvancedHeatPumpHPLib(config=config, my_simulation_parameters=simpars)

    time_off_output = cp.ComponentOutput(
        "HeatPump", heatpump.TimeOff, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("HeatPump")
    )
    # A numpy array as an element makes ``off_time != 0`` return an array whose
    # truth value is ambiguous, raising ValueError — a non-IndexError that the
    # old broad ``except Exception`` would have swallowed.
    bad_series = [60, np.array([1, 2])]
    postprocessing_results = pd.DataFrame({heatpump.TimeOff: bad_series})

    with pytest.raises(ValueError):
        heatpump.get_heatpump_cycles(output=time_off_output, index=0, postprocessing_results=postprocessing_results)
