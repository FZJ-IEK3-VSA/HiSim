""" Test for the advanced battery lib. """
# clean
import pytest
from hisim import component as cp
from hisim.components import advanced_battery_bslib
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft


@pytest.mark.base
def test_advanced_battery_bslib():
    """Performs a basic test for a single calculation of the battery lib."""
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    # ===================================================================================================================
    # Set Advanced Battery
    system_id = "SG1"  # Generic ac coupled battery storage system
    p_inv_custom = 5000  # W
    e_bat_custom = 10  # kWh
    name = "Battery"
    source_weight = 1
    charge_in_kwh = 0
    discharge_in_kwh = 0
    co2_footprint = e_bat_custom * 130.7
    cost = e_bat_custom * 535.81
    lifetime = 10
    lifetime_in_cycles = 5e3
    maintenance_costs_in_euro_per_year = 0.02 * cost
    subsidy_as_percentage_of_investment_costs = 0.0

    my_advanced_battery_config = advanced_battery_bslib.BatteryConfig(
        building_name="BUI1",
        system_id=system_id,
        custom_pv_inverter_power_generic_in_watt=p_inv_custom,
        custom_battery_capacity_generic_in_kilowatt_hour=e_bat_custom,
        name=name,
        source_weight=source_weight,
        charge_in_kwh=charge_in_kwh,
        discharge_in_kwh=discharge_in_kwh,
        device_co2_footprint_in_kg=co2_footprint,
        investment_costs_in_euro=cost,
        lifetime_in_years=lifetime,
        lifetime_in_cycles=lifetime_in_cycles,
        maintenance_costs_in_euro_per_year=maintenance_costs_in_euro_per_year,
        subsidy_as_percentage_of_investment_costs=subsidy_as_percentage_of_investment_costs
    )
    my_advanced_battery = advanced_battery_bslib.Battery(
        config=my_advanced_battery_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Set Fake Input
    loading_power_input = cp.ComponentOutput(
        "FakeLoadingPowerInput",
        "LoadingPowerInput",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
    )

    number_of_outputs = fft.get_number_of_outputs(
        [my_advanced_battery, loading_power_input]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_advanced_battery.loading_power_input_channel.source_output = loading_power_input

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_advanced_battery, loading_power_input])

    stsv.values[loading_power_input.global_index] = 4000

    timestep = 1000

    # Simulate
    my_advanced_battery.i_simulate(timestep, stsv, False)
    log.information(str(stsv.values))

    # Check if set power is charged
    assert (
        stsv.values[my_advanced_battery.ac_battery_power_channel.global_index] == 4000
    )  # noqa B101
    assert (
        stsv.values[my_advanced_battery.dc_battery_power_channel.global_index]
        == 3807.546
    )  # noqa B101
    assert (
        stsv.values[my_advanced_battery.state_of_charge_channel.global_index]
        == 0.006185227970066665
    )  # noqa B101
