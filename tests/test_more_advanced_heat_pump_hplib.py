"""Test for advanced heat pump hplib."""

import pandas as pd
import pytest
from tests import functions_for_testing as fft
from hisim import component as cp
from hisim.components.more_advanced_heat_pump.heat_pump import (
    CompressorControl,
    PositionHotWaterStorageInSystemSetup,
    MoreAdvancedHeatPumpHPLib,
    MoreAdvancedHeatPumpHPLibConfig,
    MoreAdvancedHeatPumpHPLibState,
)
from hisim.components.more_advanced_heat_pump.hplib_operation import HeatPumpRunTimers
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.config import ComponentID, SizingContext

# Heat pump configuration constants
CO2_FOOTPRINT_COEFFICIENT: float = 165.84  # kg CO2 per kW thermal power over lifetime
INVESTMENT_COST_COEFFICIENT: float = 1513.74  # EUR per kW thermal power
MAINTENANCE_COST_FRACTION: float = 0.025  # Fraction of investment cost per year


def _eight_kilowatt_heat_pump_config() -> MoreAdvancedHeatPumpHPLibConfig:
    """Builds the 8 kW air/water heat pump the postprocessing tests below run on.

    The ``air_water`` preset leaves the machine's rated thermal power and the outside
    temperature it is rated at to the building's sizing facts. These tests have no
    building, so both are stated here; 8 kW is the size the tests have always used.

    Returns:
        MoreAdvancedHeatPumpHPLibConfig: A concrete 8 kW air/water configuration.
    """
    config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("MoreAdvancedHeatPumpHPLib")
    config.set_thermal_output_power_in_watt = 8000.0
    config.heating_rod_rated_electrical_power_in_watt = 8000.0
    config.compressor_control = CompressorControl.INVERTER
    config.minimum_thermal_output_power_in_watt = 1800.0
    config.massflow_nominal_secondary_side_in_kg_per_s = 0.333
    config.heating_reference_temperature_in_celsius = -7.0
    return config


@pytest.mark.base
@pytest.mark.parametrize("thermal_power_in_watt", [0.0, 0.5, -8000.0, float("nan"), float("inf")])
def test_a_thermal_power_that_is_not_finite_or_below_1_watt_is_refused_when_the_heat_pump_is_built(
    thermal_power_in_watt: float,
) -> None:
    """Catches a stated power that builds: hplib would scale its fit to it and the buffer would follow it.

    A power stated in the configuration replaces the heating-load law, so the heat pump checks it when it is built.
    Without the check, 0 and -8000 W give the buffer beside the machine a volume of 0 l or a negative volume, NaN
    gives it NaN, and 0.5 W reaches hplib's fit as 0 W, because hplib takes the power in whole watts.
    """
    config = _eight_kilowatt_heat_pump_config()
    config.set_thermal_output_power_in_watt = thermal_power_in_watt
    config.heating_rod_rated_electrical_power_in_watt = min(thermal_power_in_watt, 9000.0)
    with pytest.raises(
        ValueError,
        match=rf"MoreAdvancedHeatPumpHPLib: the heat pump's set_thermal_output_power_in_watt must be a finite power "
        rf"of at least 1 W, got {thermal_power_in_watt!r} W",
    ):
        MoreAdvancedHeatPumpHPLib(config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 60))


@pytest.mark.base
@pytest.mark.parametrize("thermal_power_in_watt", [0.0, 0.5, -8000.0, float("nan")])
def test_a_thermal_power_that_is_not_finite_or_below_1_watt_is_refused_before_the_buffer_is_sized_from_it(
    thermal_power_in_watt: float,
) -> None:
    """Catches a sizing fact that hands an impossible power to the buffer vessel beside the machine.

    The buffer's volume law reads the heat pump's power during sizing, before any component is built, so a
    sizing-only path such as ``hisim energy-system facts`` would otherwise report a volume of 0 l or less.
    """
    config = _eight_kilowatt_heat_pump_config()
    config.set_thermal_output_power_in_watt = thermal_power_in_watt
    config.heating_rod_rated_electrical_power_in_watt = min(thermal_power_in_watt, 9000.0)
    with pytest.raises(ValueError, match="must be a finite power of at least 1 W"):
        MoreAdvancedHeatPumpHPLibConfig.sizing_facts(config, SizingContext())


@pytest.mark.base
@pytest.mark.parametrize(("thermal_power_in_watt", "set_power_in_watt"), [(8000.0, 8000), (1.0, 1)])
def test_a_heat_pump_is_built_at_its_stated_power(thermal_power_in_watt: float, set_power_in_watt: int) -> None:
    """Catches a guard that refuses a valid power or changes it: the power is passed to hplib's fit as stated.

    1 W is the smallest power accepted, so a bound moved above it fails here.
    """
    config = _eight_kilowatt_heat_pump_config()
    config.set_thermal_output_power_in_watt = thermal_power_in_watt
    config.heating_rod_rated_electrical_power_in_watt = min(thermal_power_in_watt, 9000.0)
    heat_pump = MoreAdvancedHeatPumpHPLib(
        config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 60)
    )
    assert heat_pump.p_th_set == set_power_in_watt


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
    on_off_switch_space_heating = cp.ComponentOutput(
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
    supply_temperature_set_for_dhw = cp.ComponentOutput(
        "Fake_supply_temperature_set_for_dhw",
        "Fake_supply_temperature_set_for_dhw",
        lt.LoadTypes.ANY,
        lt.Units.ANY,
        component_id=ComponentID("Fake_supply_temperature_set_for_dhw"),
    )
    t_in_primary = cp.ComponentOutput(
        "Fake_t_in_primary",
        "Fake_t_in_primary",
        lt.LoadTypes.ANY,
        lt.Units.ANY,
        component_id=ComponentID("Fake_t_in_primary"),
    )
    t_in_secondary_space_heating = cp.ComponentOutput(
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
    set_point_space_heating = cp.ComponentOutput(
        "Fake_set_point", "Fake_set_point", lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake_set_point")
    )

    # Initialize component
    heatpump_config = MoreAdvancedHeatPumpHPLibConfig(
        component_id=ComponentID(name="HeatPump"),
        model=model,
        fluid_primary_side="air",
        group_id=group_id,
        heating_reference_temperature_in_celsius=t_in,
        flow_temperature_in_celsius=t_out,
        set_thermal_output_power_in_watt=p_th_set,
        heating_rod_rated_electrical_power_in_watt=min(p_th_set, 9000.0),
        cycling_mode=True,
        minimum_idle_time_in_seconds=600,
        minimum_running_time_in_seconds=600,
        compressor_control=CompressorControl.INVERTER,
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
        time_on_heating=3600,
        time_off=0,
        time_on_cooling=0,
        on_off_previous=1,
        published_time_on_heating=3600,
        published_time_off=0,
        published_on_off_previous=1,
        cumulative_electrical_energy_tot_in_watt_hour=0,
        cumulative_thermal_energy_tot_in_watt_hour=0,
        cumulative_thermal_energy_space_heating_in_watt_hour=0,
        cumulative_thermal_energy_dhw_in_watt_hour=0,
        cumulative_electrical_energy_space_heating_in_watt_hour=0,
        cumulative_electrical_energy_dhw_in_watt_hour=0,
        counter_switch_space_heating=0,
        counter_switch_dhw=0,
        counter_onoff=0,
        delta_t_secondary_side=5,
        delta_t_primary_side=5,
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            on_off_switch_space_heating,
            on_off_switch_dhw,
            supply_temperature_set_for_dhw,
            t_in_primary,
            t_in_secondary_space_heating,
            t_in_secondary_dhw,
            t_amb,
            set_point_space_heating,
            heatpump,
        ]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    heatpump.on_off_switch_space_heating.source_output = on_off_switch_space_heating
    heatpump.on_off_switch_dhw.source_output = on_off_switch_dhw
    heatpump.supply_temperature_set_for_dhw_in_celsius_channel.source_output = supply_temperature_set_for_dhw
    heatpump.t_in_primary.source_output = t_in_primary
    heatpump.t_in_secondary_space_heating.source_output = t_in_secondary_space_heating
    heatpump.t_in_secondary_dhw.source_output = t_in_secondary_dhw
    heatpump.t_amb.source_output = t_amb
    heatpump.set_temperature_hp_space_heating.source_output = set_point_space_heating

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [
            on_off_switch_space_heating,
            on_off_switch_dhw,
            supply_temperature_set_for_dhw,
            t_in_primary,
            t_in_secondary_space_heating,
            t_in_secondary_dhw,
            t_amb,
            set_point_space_heating,
            heatpump,
        ]
    )
    stsv.values[on_off_switch_space_heating.global_index] = 1
    stsv.values[on_off_switch_dhw.global_index] = 0
    stsv.values[supply_temperature_set_for_dhw.global_index] = 60.0
    stsv.values[t_in_primary.global_index] = -7
    stsv.values[t_in_secondary_space_heating.global_index] = 47.0
    stsv.values[t_in_secondary_dhw.global_index] = 55.0
    stsv.values[t_amb.global_index] = -7
    stsv.values[set_point_space_heating.global_index] = 52.0

    # Simulation
    heatpump.i_simulate(timestep=timestep, stsv=stsv, force_convergence=force_convergence)
    # Check
    # The machine, started up an hour ago, holds its outlet at the 52 °C set point over its nominal 0.333 kg/s: the
    # 5 K from the 47 °C return carry 0.333 * 4180 * 5 = 6959.7 W, within its capacity at 52 °C, and it draws them
    # at the Carnot-efficiency COP at that outlet, 0.451 * 325.15 / (59 + 4.45) = 2.3112.
    assert stsv.values[heatpump.p_th_space_heating.global_index] == pytest.approx(6959.7, rel=1e-9)
    assert stsv.values[heatpump.p_el_space_heating.global_index] == pytest.approx(6959.7 / 2.311152876280536, rel=1e-9)
    assert stsv.values[heatpump.cop.global_index] == pytest.approx(2.311152876280536)
    assert stsv.values[heatpump.t_out_space_heating.global_index] == pytest.approx(t_out, abs=1e-9)
    assert 0.333 == stsv.values[heatpump.m_dot_space_heating.global_index]
    assert 3660 == stsv.values[heatpump.time_on_heating.global_index]
    assert 0 == stsv.values[heatpump.time_off.global_index]


@pytest.mark.base
def test_get_heatpump_cycles_counts_transitions() -> None:
    """get_heatpump_cycles counts off->on transitions and refuses any column other than TimeOff.

    A start is registered when the current ``TimeOff`` value is non-zero and the
    following value is zero (i.e. the heat pump turns back on). A one-step series
    has no transition. Any other output raises instead of counting a silent 0.
    """
    simpars = SimulationParameters.one_day_only(2017, 60)
    config = _eight_kilowatt_heat_pump_config()
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

    # Single-element series: the only element has no successor, so no start.
    single = pd.DataFrame({heatpump.TimeOff: [60]})
    assert heatpump.get_heatpump_cycles(output=time_off_output, index=0, postprocessing_results=single) == 0

    # No transitions when the series never returns to zero after a non-zero value.
    always_on_then_off = pd.DataFrame({heatpump.TimeOff: [0, 60, 60, 60]})
    assert heatpump.get_heatpump_cycles(output=time_off_output, index=0, postprocessing_results=always_on_then_off) == 0

    # Any output other than TimeOff is refused: it would otherwise count a silent 0.
    with pytest.raises(ValueError, match="TimeOff"):
        heatpump.get_heatpump_cycles(output=other_output, index=1, postprocessing_results=postprocessing_results)


@pytest.mark.base
def test_the_start_kpis_count_every_start_and_the_space_heating_starts_whatever_output_comes_after_them() -> None:
    """Catches a start KPI that ends as the count of the component's last output, or counts hot-water starts as SH.

    The KPI entries walk every output of the heat pump. The cycle count used to be assigned for each of them, and every
    output but ``TimeOff`` counted 0, so the KPI read 0 whenever an output came after ``TimeOff``, which it does in
    every simulation. The published modes 0, 1, 1, 0, 2, 2, 0, 1, 2, 1, 0, -1, 0, 1 (1 space heating, 2 hot water,
    -1 cooling, 0 off) hold five starts: into space heating at steps 1, 7 and 13, into hot water at step 4 and into
    cooling at step 11. The switch from hot water to space heating at step 9 is no start: the machine did not stop.
    The ``TimeOff`` column follows the same modes, so "Number of heat pump starts" is 5 and "Number of space-heating
    starts" is 3.
    """
    simpars = SimulationParameters.one_day_only(2017, 60)
    heatpump = MoreAdvancedHeatPumpHPLib(config=_eight_kilowatt_heat_pump_config(), my_simulation_parameters=simpars)
    published_modes = [0, 1, 1, 0, 2, 2, 0, 1, 2, 1, 0, -1, 0, 1]
    time_off_in_seconds = []
    off_streak_in_seconds = 0
    for mode in published_modes:
        off_streak_in_seconds = off_streak_in_seconds + 60 if mode == 0 else 0
        time_off_in_seconds.append(off_streak_in_seconds)
    postprocessing_results = pd.DataFrame(
        {
            heatpump.HeatPumpOnOffState: published_modes,
            heatpump.TimeOff: time_off_in_seconds,
            heatpump.TimeOnHeating: [60 if mode in (1, 2) else 0 for mode in published_modes],
        }
    )

    kpi_entries = heatpump.get_component_kpi_entries(
        all_outputs=[heatpump.heatpump_state, heatpump.time_off, heatpump.time_on_heating],
        postprocessing_results=postprocessing_results,
    )

    values = {entry.name: entry.value for entry in kpi_entries}
    assert values["Number of heat pump starts"] == 5
    assert values["Number of space-heating starts"] == 3
    assert "Number of SH heat pump cycles" not in values


@pytest.mark.base
def test_get_space_heating_starts_refuses_any_column_but_the_published_state() -> None:
    """Catches the space-heating start count reading a column that cannot tell space heating from hot water."""
    simpars = SimulationParameters.one_day_only(2017, 60)
    heatpump = MoreAdvancedHeatPumpHPLib(config=_eight_kilowatt_heat_pump_config(), my_simulation_parameters=simpars)
    postprocessing_results = pd.DataFrame({heatpump.TimeOff: [60, 0, 0]})
    with pytest.raises(ValueError, match="OnOffStateHeatpump"):
        heatpump.get_space_heating_starts(
            output=heatpump.time_off, index=0, postprocessing_results=postprocessing_results
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
    config = _eight_kilowatt_heat_pump_config()
    heatpump = MoreAdvancedHeatPumpHPLib(config=config, my_simulation_parameters=simpars)

    streak = _running_streak_counter(is_active_profile, simpars.seconds_per_timestep)
    # The component's own output objects, so the test cannot drift from how they are declared.
    heating_output = heatpump.time_on_heating
    cooling_output = heatpump.time_on_cooling
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
@pytest.mark.parametrize(
    ("mode_before", "seconds_in_mode", "expected_mode"),
    [(1, 540, 1), (1, 600, 0), (2, 540, 2), (2, 600, 0), (-1, 540, -1), (-1, 600, 0)],
    ids=[
        "space_heating_540_s_keeps_heating",
        "space_heating_600_s_stops",
        "hot_water_540_s_keeps_heating",
        "hot_water_600_s_stops",
        "cooling_540_s_keeps_cooling",
        "cooling_600_s_stops",
    ],
)
def test_a_machine_asked_to_stop_runs_on_until_the_default_minimum_running_time_of_600_s(
    mode_before: int, seconds_in_mode: int, expected_mode: int
) -> None:
    """The preset's minimum running time is 600 s, and a stop is obeyed exactly at it, not one step later.

    A machine that has heated (1 space heating, 2 hot water) or cooled (-1) for 540 s and is asked to stop (0) keeps
    its mode; at 600 s it stops. Catches a default that drifts from the sourced 600 s (it was 3600 s, which held
    every 60 s run on for an hour) and a comparison at the edge that cuts the minimum short or holds it one step longer.
    """
    config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
    minimum_running_time_in_seconds = config.minimum_running_time_in_seconds
    minimum_idle_time_in_seconds = config.minimum_idle_time_in_seconds
    assert minimum_running_time_in_seconds == 600 and minimum_idle_time_in_seconds == 600
    heating_seconds = seconds_in_mode if mode_before in (1, 2) else 0
    cooling_seconds = seconds_in_mode if mode_before == -1 else 0
    timers = HeatPumpRunTimers(
        time_on_heating_in_seconds=heating_seconds, time_on_cooling_in_seconds=cooling_seconds, time_off_in_seconds=0
    )
    mode = MoreAdvancedHeatPumpHPLib.on_off_after_minimum_times(
        on_off=0,
        on_off_previous=mode_before,
        timers=timers,
        minimum_running_time_in_seconds=minimum_running_time_in_seconds,
        minimum_idle_time_in_seconds=minimum_idle_time_in_seconds,
    )
    assert mode == expected_mode


@pytest.mark.base
@pytest.mark.parametrize(
    ("requested_mode", "seconds_off", "expected_mode"),
    [(1, 540, 0), (1, 600, 1), (2, 540, 0), (2, 600, 2), (-1, 540, 0), (-1, 600, -1)],
    ids=[
        "space_heating_after_540_s_stays_off",
        "space_heating_after_600_s_starts",
        "hot_water_after_540_s_stays_off",
        "hot_water_after_600_s_starts",
        "cooling_after_540_s_stays_off",
        "cooling_after_600_s_starts",
    ],
)
def test_a_machine_asked_to_start_rests_until_the_default_minimum_idle_time_of_600_s(
    requested_mode: int, seconds_off: int, expected_mode: int
) -> None:
    """The preset's minimum idle time is 600 s, and a start is obeyed exactly at it, not one step later.

    A machine that has been off for 540 s and is asked to heat space (1), heat water (2) or cool (-1) stays off; at
    600 s it starts in the requested mode. Catches a default that drifts from the sourced 600 s (it was 3600 s, which
    held the machine off through the evening's hot-water draws at 60 s) and a comparison at the edge that cuts the
    rest short or holds it one step longer.
    """
    config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
    minimum_running_time_in_seconds = config.minimum_running_time_in_seconds
    minimum_idle_time_in_seconds = config.minimum_idle_time_in_seconds
    assert minimum_running_time_in_seconds == 600 and minimum_idle_time_in_seconds == 600
    timers = HeatPumpRunTimers(time_on_heating_in_seconds=0, time_on_cooling_in_seconds=0, time_off_in_seconds=seconds_off)
    mode = MoreAdvancedHeatPumpHPLib.on_off_after_minimum_times(
        on_off=requested_mode,
        on_off_previous=0,
        timers=timers,
        minimum_running_time_in_seconds=minimum_running_time_in_seconds,
        minimum_idle_time_in_seconds=minimum_idle_time_in_seconds,
    )
    assert mode == expected_mode
