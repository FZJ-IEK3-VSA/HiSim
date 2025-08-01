"""Test for advanced heat pump hplib."""

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


@pytest.mark.base
def test_heat_pump_hplib():
    """Test heat pump hplib."""

    # Definitions for HeatPump init
    model: str = "Generic"
    group_id: int = 1
    t_in: Quantity[float, Celsius] = Quantity(-7, Celsius)
    t_out: Quantity[float, Celsius] = Quantity(52, Celsius)
    p_th_set: Quantity[float, Watt] = Quantity(10000, Watt)
    simpars = SimulationParameters.one_day_only(2017, 60)
    # Definitions for i_simulate
    timestep = 1
    force_convergence = False

    # Create fake component outputs as inputs for simulation
    on_off_switch = cp.ComponentOutput("Fake_on_off_switch", "Fake_on_off_switch", lt.LoadTypes.ANY, lt.Units.ANY)
    t_in_primary = cp.ComponentOutput("Fake_t_in_primary", "Fake_t_in_primary", lt.LoadTypes.ANY, lt.Units.ANY)
    t_in_secondary = cp.ComponentOutput("Fake_t_in_secondary", "Fake_t_in_secondary", lt.LoadTypes.ANY, lt.Units.ANY)
    t_amb = cp.ComponentOutput("Fake_t_amb", "Fake_t_amb", lt.LoadTypes.ANY, lt.Units.ANY)

    # Initialize component
    heatpump_config = HeatPumpHplibConfig(
        building_name="BUI1",
        name="Heat Pump",
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
    heatpump = HeatPumpHplib(config=heatpump_config, my_simulation_parameters=simpars)
    heatpump.state = HeatPumpState(time_on_heating=0, time_off=0, time_on_cooling=0, on_off_previous=1)

    number_of_outputs = fft.get_number_of_outputs([on_off_switch, t_in_primary, t_in_secondary, t_amb, heatpump])
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
