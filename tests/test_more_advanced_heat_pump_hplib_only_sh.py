"""Test for advanced heat pump hplib."""

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
from hisim import log
from hisim.units import Quantity, Watt, Celsius, Seconds, Kilogram, Euro, Years, KilogramPerSecond, Unitless


@pytest.mark.base
def test_heat_pump_hplib_new():
    """Test heat pump hplib."""

    # Definitions for HeatPump init
    model: str = "Generic"
    group_id: int = 1
    t_in: Quantity[float, Celsius] = Quantity(-7, Celsius)
    t_out: Quantity[float, Celsius] = Quantity(52, Celsius)
    p_th_set: Quantity[float, Watt] = Quantity(10000, Watt)
    with_domestic_hot_water_preparation: bool = False
    simpars = SimulationParameters.one_day_only(2017, 60)
    # Definitions for i_simulate
    timestep = 1
    force_convergence = False

    # Create fake component outputs as inputs for simulation
    on_off_switch_sh = cp.ComponentOutput("Fake_on_off_switch", "Fake_on_off_switch", lt.LoadTypes.ANY, lt.Units.ANY)
    t_in_primary = cp.ComponentOutput("Fake_t_in_primary", "Fake_t_in_primary", lt.LoadTypes.ANY, lt.Units.ANY)
    t_in_secondary_sh = cp.ComponentOutput(
        "Fake_t_in_secondary_hot_water", "Fake_t_in_secondary_hot_water", lt.LoadTypes.ANY, lt.Units.ANY
    )
    t_amb = cp.ComponentOutput("Fake_t_amb", "Fake_t_amb", lt.LoadTypes.ANY, lt.Units.ANY)

    # Initialize component
    heatpump_config = MoreAdvancedHeatPumpHPLibConfig(
        building_name="BUI1",
        name="Heat Pump",
        model=model,
        fluid_primary_side="air",
        group_id=group_id,
        heating_reference_temperature_in_celsius=t_in,
        flow_temperature_in_celsius=t_out,
        set_thermal_output_power_in_watt=p_th_set,
        cycling_mode=True,
        minimum_idle_time_in_seconds=Quantity(600, Seconds),
        minimum_running_time_in_seconds=Quantity(600, Seconds),
        minimum_thermal_output_power_in_watt=Quantity(1500, Watt),
        position_hot_water_storage_in_system=PositionHotWaterStorageInSystemSetup.PARALLEL,
        with_domestic_hot_water_preparation=with_domestic_hot_water_preparation,
        passive_cooling_with_brine=False,
        electrical_input_power_brine_pump_in_watt=None,
        massflow_nominal_secondary_side_in_kg_per_s=Quantity(0.333, KilogramPerSecond),
        massflow_nominal_primary_side_in_kg_per_s=0,
        specific_heat_capacity_of_primary_fluid=0,
        device_co2_footprint_in_kg=Quantity(p_th_set.value * 1e-3 * 165.84, Kilogram),
        investment_costs_in_euro=Quantity(p_th_set.value * 1e-3 * 1513.74, Euro),
        lifetime_in_years=Quantity(10, Years),
        maintenance_costs_in_euro_per_year=Quantity(0.025 * p_th_set.value * 1e-3 * 1513.74, Euro),
        subsidy_as_percentage_of_investment_costs=Quantity(0.3, Unitless)
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
        delta_t_primary_side=5
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            on_off_switch_sh,
            t_in_primary,
            t_in_secondary_sh,
            t_amb,
            heatpump,
        ]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    heatpump.on_off_switch_sh.source_output = on_off_switch_sh
    heatpump.t_in_primary.source_output = t_in_primary
    heatpump.t_in_secondary_sh.source_output = t_in_secondary_sh
    heatpump.t_amb.source_output = t_amb

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [
            on_off_switch_sh,
            t_in_primary,
            t_in_secondary_sh,
            t_amb,
            heatpump,
        ]
    )
    stsv.values[on_off_switch_sh.global_index] = 1
    stsv.values[t_in_primary.global_index] = -7
    stsv.values[t_in_secondary_sh.global_index] = 47.0
    stsv.values[t_amb.global_index] = -7

    # Simulation
    heatpump.i_simulate(timestep=timestep, stsv=stsv, force_convergence=force_convergence)
    log.information(str(stsv.values))
    # Check
    assert p_th_set.value == stsv.values[heatpump.p_th_sh.global_index]
    assert 7074.033573088874 == stsv.values[heatpump.p_el_sh.global_index]
    assert 1.4136206588052005 == stsv.values[heatpump.cop.global_index]
    assert t_out.value == stsv.values[heatpump.t_out_sh.global_index]
    assert 0.47619047619047616 == stsv.values[heatpump.m_dot_sh.global_index]
    assert 60 == stsv.values[heatpump.time_on_heating.global_index]
    assert 0 == stsv.values[heatpump.time_off.global_index]
